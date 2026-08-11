from conet.observability.tracing import audit
from conet.persistence.store import Store
from conet.protocols.events.bus import EventBus
from conet.sdk.manifests import AgentManifest, SkillDef


class DuplicateAgentError(Exception):
    """Raised when register_agent is called for a name that is already active (FR-002)."""


class UnknownAgentError(Exception):
    """Raised when an operation targets an agent_id that isn't registered."""


class Registry:
    """Agents join, renew, publish Skills, and leave. Enforces unique identity.

    agent_id is the manifest's `name` — LLD-01 already requires it be
    unique within the network, so there is no separate ID to mint.
    """

    def __init__(self, store: Store, event_bus: EventBus) -> None:
        self._store = store
        self._event_bus = event_bus

    async def register_agent(self, manifest: AgentManifest) -> str:
        if await self._store.is_agent_active(manifest.name):
            raise DuplicateAgentError(f"agent '{manifest.name}' is already registered and active")

        await self._store.upsert_agent(manifest)
        await self._event_bus.publish('conet.agent.registered', {'agent_id': manifest.name})
        await audit(self._store, actor=manifest.name, action='register', resource=manifest.name, outcome='OK')
        return manifest.name

    async def renew_lease(self, agent_id: str) -> bool:
        if not await self._store.is_agent_active(agent_id):
            return False

        manifest = await self._store.get_agent(agent_id)
        if manifest is None:
            # is_agent_active() just said yes; a concurrent unregister could
            # in principle race us here. Treat it the same as "not active".
            return False
        await self._store.upsert_agent(manifest)
        await self._event_bus.publish('conet.agent.renewed', {'agent_id': agent_id})
        await audit(self._store, actor=agent_id, action='renew_lease', resource=agent_id, outcome='OK')
        return True

    async def unregister_agent(self, agent_id: str) -> None:
        await self._store.deactivate_agent(agent_id)
        await self._event_bus.publish('conet.agent.unregistered', {'agent_id': agent_id})
        await audit(self._store, actor=agent_id, action='unregister', resource=agent_id, outcome='OK')

    async def publish_skills(self, agent_id: str, skills: list[SkillDef]) -> None:
        manifest = await self._store.get_agent(agent_id)
        if manifest is None:
            raise UnknownAgentError(f"agent '{agent_id}' is not registered")
        if manifest.role == 'worker' and not skills:
            raise ValueError("role='worker' requires at least one skill")

        manifest.skills = skills
        await self._store.upsert_agent(manifest)
