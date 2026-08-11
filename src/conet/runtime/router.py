import logging
import uuid

import grpc
from google.protobuf.struct_pb2 import Struct

from conet.control.discovery import Discovery
from conet.control.policy import PolicyEngine
from conet.protocols.grpc import skillruntime_pb2 as pb2
from conet.protocols.grpc import skillruntime_pb2_grpc as pb2_grpc
from conet.sdk.manifests import AgentManifest

logger = logging.getLogger(__name__)


class NoProviderAvailableError(Exception):
    """Raised when no authorized, active provider exists for a skill_id (the 0-provider case)."""


def _channel_target(endpoint: str) -> str:
    return endpoint.split('://', 1)[-1]  # "grpc://host:port" -> "host:port"


class Router:
    """Choose one provider among many, and execute a Skill call against it.

    'Basic selection' for v0.1 (Feature Plan F5, Stage B): a deterministic
    choice (sorted by name) with retry/failover only for read_only skills —
    "non-idempotent Skills are never auto-retried" (FR-012, FR-013).
    Health/capacity-aware ranking and full failover are Stage C.
    """

    def __init__(self, discovery: Discovery, policy_engine: PolicyEngine) -> None:
        self._discovery = discovery
        self._policy = policy_engine

    async def select_providers(
        self, requester: str, skill_id: str, version: str | None = None,
    ) -> list[AgentManifest]:
        candidates = await self._discovery.find_skill(requester, skill_id, version)
        ordered = sorted(candidates, key=lambda m: m.name)
        logger.info(
            'routing: %d candidate(s) for skill_id=%s requester=%s -> %s',
            len(ordered), skill_id, requester, [m.name for m in ordered],
        )
        return ordered

    async def execute(
        self, requester: str, skill_id: str, payload: dict, version: str | None = None, timeout: float | None = None,
    ) -> pb2.SkillResponse:
        providers = await self.select_providers(requester, skill_id, version)
        if not providers:
            raise NoProviderAvailableError(f'no active, authorized provider for skill_id={skill_id!r}')

        skill_def = next(s for s in providers[0].skills if s.skill_id == skill_id)
        retryable = skill_def.side_effects == 'read_only'
        attempts = providers if retryable else providers[:1]

        response: pb2.SkillResponse | None = None
        last_error: grpc.aio.AioRpcError | None = None
        for provider in attempts:
            channel = grpc.aio.insecure_channel(_channel_target(provider.endpoint))
            try:
                stub = pb2_grpc.SkillRuntimeStub(channel)
                token = self._policy.mint_auth_context(requester, skill_id)
                input_struct = Struct()
                input_struct.update(payload)
                request = pb2.SkillRequest(
                    skill_id=skill_id, task_id=str(uuid.uuid4()), auth_context=token, input=input_struct,
                )
                response = await stub.Execute(request, timeout=timeout)
            except grpc.aio.AioRpcError as exc:
                logger.warning('routing: provider=%s unreachable for skill_id=%s (%s)', provider.name, skill_id, exc.code())
                last_error = exc
                response = None
                continue
            finally:
                await channel.close()

            if response.status == pb2.OK or not retryable:
                return response
            logger.warning(
                'routing: provider=%s returned non-OK status=%s for skill_id=%s, trying next',
                provider.name, response.status, skill_id,
            )

        if response is not None:
            return response  # last non-OK response from a retryable skill that exhausted all providers
        assert last_error is not None
        raise last_error
