from conet.control.policy import PolicyEngine
from conet.observability.tracing import audit
from conet.persistence.store import Store
from conet.sdk.manifests import AgentManifest


class Discovery:
    """Find providers by Skill name — permission-aware, so unauthorized
    providers never appear in the result (FR-006)."""

    def __init__(self, store: Store, policy_engine: PolicyEngine) -> None:
        self._store = store
        self._policy = policy_engine

    async def find_skill(self, requester: str, skill_id: str, version: str | None = None) -> list[AgentManifest]:
        candidates = await self._store.list_active_providers(skill_id)

        if version is not None:
            candidates = [
                c for c in candidates
                if any(s.skill_id == skill_id and s.version == version for s in c.skills)
            ]

        authorized = []
        for candidate in candidates:
            if await self._policy.authorize(requester, skill_id, 'invoke'):
                authorized.append(candidate)
            else:
                # FR-022: a denial is security-significant and must be audited.
                await audit(self._store, actor=requester, action='find_skill', resource=skill_id, outcome='DENIED')
        return authorized

    async def describe_provider(self, agent_id: str) -> AgentManifest | None:
        return await self._store.get_agent(agent_id)
