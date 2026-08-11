import logging
from datetime import datetime, timedelta, timezone

from conet.control.teams import TeamService
from conet.observability.tracing import audit
from conet.persistence.store import Store
from conet.sdk.manifests import Approval

logger = logging.getLogger(__name__)


class UnknownApprovalError(Exception):
    """Raised when approve/reject targets an approval_id that doesn't exist."""


class UnknownTaskError(Exception):
    """Raised when request_approval targets a task_id that isn't tracked in the Store."""


class NotAnAuthorizedApproverError(Exception):
    """Raised when the decider is neither an assigned approver nor holds the approve_task role."""


class ApprovalAlreadyDecidedError(Exception):
    """Raised when approve/reject targets an approval that is no longer PENDING."""


class ApprovalWorkflow:
    """A policy can place a high-risk task into WAITING_APPROVAL; an
    authorized human approves or rejects with an auditable decision; the
    task resumes or is rejected. Approvals expire (FR-015, FR-016).

    Live gating of an in-flight Execute() call on a pending approval is a
    further runtime-integration step this unit does not cover — this is
    the service layer: it owns Approval/Task state and the audit trail.
    """

    def __init__(self, store: Store, team_service: TeamService | None = None) -> None:
        self._store = store
        self._teams = team_service

    async def request_approval(
        self, task_id: str, approvers: list[str], ttl_seconds: int = 3600, policy_id: str | None = None,
    ) -> Approval:
        task = await self._store.get_task(task_id)
        if task is None:
            raise UnknownTaskError(f'task {task_id!r} is not tracked in the Store')

        approval = Approval(
            task_id=task_id, policy_id=policy_id, approvers=approvers,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        )
        await self._store.save_approval(approval)

        task.state = 'WAITING_APPROVAL'
        await self._store.save_task(task)

        await audit(
            self._store, actor=task.requester, action='request_approval', resource=task_id,
            outcome='OK', trace_id=task.trace_id, metadata={'approval_id': approval.approval_id, 'approvers': approvers},
        )
        logger.info('approval %s requested for task %s (approvers=%s)', approval.approval_id, task_id, approvers)
        return approval

    async def _authorize_decider(self, approval: Approval, decided_by: str) -> None:
        if decided_by not in approval.approvers:
            raise NotAnAuthorizedApproverError(f'{decided_by!r} is not an assigned approver for {approval.approval_id!r}')
        if self._teams is not None and not await self._teams.can(decided_by, 'approve_task'):
            raise NotAnAuthorizedApproverError(f'{decided_by!r} does not currently hold the approve_task permission')

    async def _load_pending(self, approval_id: str) -> Approval:
        approval = await self._store.get_approval(approval_id)
        if approval is None:
            raise UnknownApprovalError(f'approval {approval_id!r} not found')
        if approval.state != 'PENDING':
            raise ApprovalAlreadyDecidedError(f'approval {approval_id!r} is already {approval.state}')
        return approval

    async def approve(self, approval_id: str, decided_by: str, metadata: dict | None = None) -> Approval:
        approval = await self._load_pending(approval_id)
        await self._authorize_decider(approval, decided_by)

        approval.state = 'APPROVED'
        approval.decision_metadata = {'decided_by': decided_by, **(metadata or {})}
        await self._store.save_approval(approval)

        task = await self._store.get_task(approval.task_id)
        if task is not None:
            task.state = 'ROUTING'  # resumes toward execution
            await self._store.save_task(task)

        await audit(
            self._store, actor=decided_by, action='approve', resource=approval.task_id,
            outcome='OK', trace_id=task.trace_id if task else None, metadata={'approval_id': approval_id},
        )
        logger.info('approval %s approved by %s', approval_id, decided_by)
        return approval

    async def reject(self, approval_id: str, decided_by: str, metadata: dict | None = None) -> Approval:
        approval = await self._load_pending(approval_id)
        await self._authorize_decider(approval, decided_by)

        approval.state = 'REJECTED'
        approval.decision_metadata = {'decided_by': decided_by, **(metadata or {})}
        await self._store.save_approval(approval)

        task = await self._store.get_task(approval.task_id)
        if task is not None:
            task.state = 'REJECTED'
            await self._store.save_task(task)

        await audit(
            self._store, actor=decided_by, action='reject', resource=approval.task_id,
            outcome='DENIED', trace_id=task.trace_id if task else None, metadata={'approval_id': approval_id},
        )
        logger.info('approval %s rejected by %s', approval_id, decided_by)
        return approval

    async def expire_overdue(self) -> list[Approval]:
        """Meant to be called periodically (e.g. by a background loop in the
        dashboard/CLI process). Marks every PENDING approval past its
        expires_at as EXPIRED and rejects the underlying task."""
        now = datetime.now(timezone.utc)
        expired: list[Approval] = []
        for approval in await self._store.list_pending_approvals():
            if approval.expires_at > now:
                continue
            approval.state = 'EXPIRED'
            await self._store.save_approval(approval)

            task = await self._store.get_task(approval.task_id)
            if task is not None:
                task.state = 'REJECTED'
                await self._store.save_task(task)

            await audit(
                self._store, actor='system', action='expire_approval', resource=approval.task_id,
                outcome='DENIED', trace_id=task.trace_id if task else None, metadata={'approval_id': approval.approval_id},
            )
            logger.info('approval %s expired', approval.approval_id)
            expired.append(approval)
        return expired
