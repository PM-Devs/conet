import asyncio
import logging
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

import grpc
from google.protobuf.struct_pb2 import Struct
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from conet.control.approvals import ApprovalWorkflow
from conet.control.policy import PolicyEngine
from conet.observability.tracing import audit
from conet.persistence.store import Store
from conet.protocols.grpc import skillruntime_pb2 as pb2
from conet.protocols.grpc import skillruntime_pb2_grpc as pb2_grpc
from conet.sdk.manifests import AgentManifest, AuditOutcome, SkillDef, Task, TaskState

logger = logging.getLogger(__name__)

_TERMINAL_OUTCOME: dict[TaskState, AuditOutcome] = {'COMPLETED': 'OK', 'FAILED': 'FAILED', 'CANCELLED': 'CANCELLED'}


@runtime_checkable
class SkillAdapter(Protocol):
    """The framework-specific side of the NIC contract (LLD-01 §4).
    Only invoke() is required; stream() is optional."""

    async def invoke(self, skill_id: str, payload: dict) -> dict: ...

    async def stream(self, skill_id: str, payload: dict):  # -> AsyncIterator[dict]
        ...


class SkillServer(pb2_grpc.SkillRuntimeServicer):
    """The real Skill-execution server: validates, runs the adapter, returns
    typed results. One instance wraps one agent's manifest + adapter."""

    def __init__(
        self, manifest: AgentManifest, adapter: SkillAdapter, policy_engine: PolicyEngine, store: Store | None = None,
        approvals: ApprovalWorkflow | None = None, approvers: list[str] | None = None,
        approval_ttl_seconds: int = 3600, approval_poll_interval_seconds: float = 1.0,
    ) -> None:
        self._provider_name = manifest.name
        self._skills_by_id = {skill.skill_id: skill for skill in manifest.skills}
        self._adapter = adapter
        self._policy = policy_engine
        self._store = store
        # F7 auto-gating: an unsafe_write Skill pauses at the approval queue
        # before invoke() runs, iff both an ApprovalWorkflow and at least one
        # approver are configured. Neither is required -- omitting them
        # keeps every existing Skill's behavior exactly as it was.
        self._approvals = approvals
        self._approvers = approvers or []
        self._approval_ttl_seconds = approval_ttl_seconds
        self._approval_poll_interval_seconds = approval_poll_interval_seconds
        self._running_tasks: dict[str, asyncio.Task] = {}

    def _requires_approval(self, skill: SkillDef) -> bool:
        return self._approvals is not None and bool(self._approvers) and skill.side_effects == 'unsafe_write'

    async def _await_approval(self, request) -> pb2.SkillResponse | None:
        """Blocks until a human decides, or the approval expires. Returns
        None if approved (the caller should proceed to invoke the adapter),
        or a terminal SkillResponse to return directly otherwise.

        Task/Approval state and their audit trail stay owned end to end by
        ApprovalWorkflow (request_approval/approve/reject/expire_overdue) --
        this only polls for the outcome and translates it into a gRPC
        response; it never mutates that state itself.
        """
        assert self._approvals is not None and self._store is not None
        approval = await self._approvals.request_approval(
            task_id=request.task_id, approvers=self._approvers, ttl_seconds=self._approval_ttl_seconds,
        )
        while True:
            current = await self._store.get_approval(approval.approval_id)
            assert current is not None, 'the approval this method just created must exist'
            if current.state == 'APPROVED':
                return None
            if current.state == 'REJECTED':
                return pb2.SkillResponse(status=pb2.DENIED, error_detail='rejected by approver')
            if current.state == 'EXPIRED':
                return pb2.SkillResponse(status=pb2.TIMED_OUT, error_detail='approval expired without a decision')
            if current.expires_at <= datetime.now(timezone.utc):
                await self._approvals.expire_overdue()
                return pb2.SkillResponse(status=pb2.TIMED_OUT, error_detail='approval expired without a decision')
            await asyncio.sleep(self._approval_poll_interval_seconds)

    async def _authorize_request(self, request) -> tuple[dict | None, str | None]:
        """Returns (claims, error_detail); error_detail is None iff
        authorized. claims is None for the failure modes where the subject
        isn't trustworthy yet (bad signature, skill_id mismatch) — but once
        the token itself checks out, claims is returned even on an
        authorize() denial, so the denial's audit record can name who was
        actually denied instead of 'unknown' (FR-022).

        Verifying the token's signature is not enough on its own — nothing
        stops something upstream from minting a token without having called
        authorize() first. FR-014 requires policy evaluation before
        execution, not just before discovery, so re-check it here too.
        """
        claims = self._policy.verify_auth_context(request.auth_context)
        if claims is None:
            return None, 'invalid or expired auth_context'
        if claims.get('skill_id') != request.skill_id:
            return None, 'auth_context does not authorize this skill_id'
        if request.skill_id not in self._skills_by_id:
            return None, f'unknown skill_id {request.skill_id!r}'
        if not await self._policy.authorize(claims['sub'], request.skill_id, 'invoke'):
            return claims, f"{claims['sub']!r} is not authorized to invoke {request.skill_id!r}"
        return claims, None

    async def _record(self, request, requester: str, state: TaskState) -> None:
        """Saves the Task's current state, and — for terminal states — an
        AuditEvent explaining the outcome (FR-022), both carrying the same
        trace_id the request came in with."""
        if self._store is None:
            return
        await self._store.save_task(Task(
            task_id=request.task_id, requester=requester, provider=self._provider_name,
            skill_id=request.skill_id, state=state, trace_id=request.trace_id or None,
            idempotency_key=request.idempotency_key or None,
        ))
        outcome = _TERMINAL_OUTCOME.get(state)
        if outcome:
            await audit(
                self._store, actor=requester, action=f'invoke:{request.skill_id}', resource=request.skill_id,
                outcome=outcome, trace_id=request.trace_id or None,
            )

    async def _record_denial(self, request, subject: str | None, denial: str) -> None:
        if self._store is None:
            return
        await audit(
            self._store, actor=subject or 'unknown', action=f'invoke:{request.skill_id}', resource=request.skill_id,
            outcome='DENIED', trace_id=request.trace_id or None, metadata={'reason': denial},
        )

    def _claim_task(self, task_id: str) -> None:
        current = asyncio.current_task()
        assert current is not None, 'Execute/ExecuteStream must run inside a Task (true for all grpc.aio handlers)'
        self._running_tasks[task_id] = current

    async def Execute(self, request, context) -> pb2.SkillResponse:
        claims, denial = await self._authorize_request(request)
        if denial:
            await self._record_denial(request, claims['sub'] if claims else None, denial)
            return pb2.SkillResponse(status=pb2.DENIED, error_detail=denial)
        assert claims is not None

        skill = self._skills_by_id[request.skill_id]
        payload = dict(request.input)
        requester = claims['sub']
        try:
            skill.validate_input(payload)
        except Exception as exc:
            logger.exception('input validation failed for skill_id=%s task_id=%s', request.skill_id, request.task_id)
            await self._record(request, requester, state='FAILED')
            return pb2.SkillResponse(status=pb2.FAILED, error_detail=f'input validation failed: {exc}')

        await self._record(request, requester, state='RUNNING')
        self._claim_task(request.task_id)
        try:
            if self._requires_approval(skill):
                gate_response = await self._await_approval(request)
                if gate_response is not None:
                    return gate_response
                await self._record(request, requester, state='RUNNING')  # resumed after approval

            result = await self._adapter.invoke(request.skill_id, payload)
        except asyncio.CancelledError:
            await self._record(request, requester, state='CANCELLED')
            return pb2.SkillResponse(status=pb2.CANCELLED, error_detail='cancelled')
        except Exception as exc:
            logger.exception('adapter.invoke failed for skill_id=%s task_id=%s', request.skill_id, request.task_id)
            await self._record(request, requester, state='FAILED')
            return pb2.SkillResponse(status=pb2.FAILED, error_detail=str(exc))
        finally:
            self._running_tasks.pop(request.task_id, None)

        try:
            skill.validate_output(result)
        except Exception as exc:
            logger.exception('output validation failed for skill_id=%s task_id=%s', request.skill_id, request.task_id)
            await self._record(request, requester, state='FAILED')
            return pb2.SkillResponse(status=pb2.FAILED, error_detail=f'output validation failed: {exc}')

        await self._record(request, requester, state='COMPLETED')
        output = Struct()
        output.update(result)
        return pb2.SkillResponse(status=pb2.OK, output=output)

    async def ExecuteStream(self, request, context):
        claims, denial = await self._authorize_request(request)
        if denial:
            await self._record_denial(request, claims['sub'] if claims else None, denial)
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details(denial)
            return
        assert claims is not None

        skill = self._skills_by_id[request.skill_id]
        payload = dict(request.input)
        requester = claims['sub']
        try:
            skill.validate_input(payload)
        except Exception as exc:
            logger.exception('input validation failed for skill_id=%s task_id=%s', request.skill_id, request.task_id)
            await self._record(request, requester, state='FAILED')
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(f'input validation failed: {exc}')
            return

        await self._record(request, requester, state='RUNNING')
        self._claim_task(request.task_id)
        try:
            seq = 0
            async for chunk in self._adapter.stream(request.skill_id, payload):
                data = Struct()
                data.update(chunk)
                yield pb2.SkillChunk(seq=seq, data=data)
                seq += 1
            await self._record(request, requester, state='COMPLETED')
        except asyncio.CancelledError:
            await self._record(request, requester, state='CANCELLED')
            raise
        finally:
            self._running_tasks.pop(request.task_id, None)

    async def Cancel(self, request, context) -> pb2.CancelAck:
        task = self._running_tasks.get(request.task_id)
        if task is None:
            return pb2.CancelAck(acknowledged=False)
        task.cancel()
        return pb2.CancelAck(acknowledged=True)


_HEALTH_SERVICE_NAME = 'conet.runtime.SkillRuntime'


async def serve(
    manifest: AgentManifest, adapter: SkillAdapter, policy_engine: PolicyEngine, port: int, store: Store | None = None,
    approvals: ApprovalWorkflow | None = None, approvers: list[str] | None = None,
    approval_ttl_seconds: int = 3600, approval_poll_interval_seconds: float = 1.0,
) -> grpc.aio.Server:
    server = grpc.aio.server()
    skill_server = SkillServer(
        manifest, adapter, policy_engine, store,
        approvals=approvals, approvers=approvers,
        approval_ttl_seconds=approval_ttl_seconds, approval_poll_interval_seconds=approval_poll_interval_seconds,
    )
    pb2_grpc.add_SkillRuntimeServicer_to_server(skill_server, server)

    # F8 (health & lifecycle): standard gRPC health checking (proven in Stage A's
    # A2 prototype), so the runtime — and a future Router/F5 — can tell a live
    # provider from an unhealthy one (FR-019), not just a lease-alive one.
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set(_HEALTH_SERVICE_NAME, health_pb2.HealthCheckResponse.SERVING)
    health_servicer.set('', health_pb2.HealthCheckResponse.SERVING)  # overall server health

    server.add_insecure_port(f'[::]:{port}')
    await server.start()
    return server
