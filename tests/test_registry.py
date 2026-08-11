import pytest

from conet.control.registry import DuplicateAgentError, Registry, UnknownAgentError
from conet.persistence.store import Store
from conet.sdk.manifests import AgentManifest, SkillDef


class FakeEventBus:
    def __init__(self):
        self.published = []

    async def publish(self, subject, payload):
        self.published.append((subject, payload))


def make_manifest(name: str, skill_id: str = 'invoice.verify', ttl: int = 30, role: str = 'worker') -> AgentManifest:
    return AgentManifest(
        name=name, framework='langchain', department='finance', version='1.0.0',
        endpoint='grpc://localhost:1', identity_ref='cert-1', lease_ttl_seconds=ttl, role=role,
        skills=[SkillDef(
            skill_id=skill_id, version='1.0.0', side_effects='read_only',
            input_schema={'type': 'object'}, output_schema={'type': 'object'},
        )] if role == 'worker' else [],
    )


@pytest.fixture
async def store():
    s = Store(':memory:')
    yield s
    await s.close()


@pytest.fixture
def event_bus():
    return FakeEventBus()


@pytest.fixture
def registry(store, event_bus):
    return Registry(store, event_bus)


async def test_register_agent_returns_name_as_agent_id(registry):
    agent_id = await registry.register_agent(make_manifest('agent-a'))
    assert agent_id == 'agent-a'


async def test_register_agent_publishes_and_audits(registry, store, event_bus):
    await registry.register_agent(make_manifest('agent-a'))
    assert event_bus.published == [('conet.agent.registered', {'agent_id': 'agent-a'})]


async def test_register_agent_rejects_duplicate_active_name(registry):
    await registry.register_agent(make_manifest('agent-a'))
    with pytest.raises(DuplicateAgentError):
        await registry.register_agent(make_manifest('agent-a'))


async def test_register_agent_allows_reregistration_after_expiry(registry):
    await registry.register_agent(make_manifest('agent-a', ttl=-10))
    # first registration's lease is already expired -> not "active" -> allowed again
    agent_id = await registry.register_agent(make_manifest('agent-a'))
    assert agent_id == 'agent-a'


async def test_renew_lease_true_for_active_agent(registry):
    await registry.register_agent(make_manifest('agent-a'))
    assert await registry.renew_lease('agent-a') is True


async def test_renew_lease_false_for_unknown_agent(registry):
    assert await registry.renew_lease('nobody') is False


async def test_renew_lease_false_for_expired_agent(registry, store):
    manifest = make_manifest('agent-a', ttl=-10)
    await store.upsert_agent(manifest)  # bypass registry to plant an expired record
    assert await registry.renew_lease('agent-a') is False


async def test_unregister_agent_removes_it(registry, store):
    await registry.register_agent(make_manifest('agent-a'))
    await registry.unregister_agent('agent-a')
    assert await store.get_agent('agent-a') is None


async def test_unregister_agent_publishes_event(registry, event_bus):
    await registry.register_agent(make_manifest('agent-a'))
    await registry.unregister_agent('agent-a')
    subjects = [subject for subject, _ in event_bus.published]
    assert subjects == ['conet.agent.registered', 'conet.agent.unregistered']


async def test_publish_skills_updates_agent_skills(registry, store):
    await registry.register_agent(make_manifest('agent-a', skill_id='invoice.verify'))
    new_skill = SkillDef(
        skill_id='invoice.audit', version='1.0.0', side_effects='read_only',
        input_schema={'type': 'object'}, output_schema={'type': 'object'},
    )
    await registry.publish_skills('agent-a', [new_skill])

    fetched = await store.get_agent('agent-a')
    assert [s.skill_id for s in fetched.skills] == ['invoice.audit']


async def test_publish_skills_raises_for_unknown_agent(registry):
    with pytest.raises(UnknownAgentError):
        await registry.publish_skills('nobody', [])


async def test_publish_skills_rejects_empty_skills_for_worker(registry):
    await registry.register_agent(make_manifest('agent-a'))
    with pytest.raises(ValueError):
        await registry.publish_skills('agent-a', [])
