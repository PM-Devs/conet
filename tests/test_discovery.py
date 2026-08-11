import os

import pytest

from conet.control.discovery import Discovery
from conet.control.policy import PolicyEngine
from conet.persistence.store import Store
from conet.sdk.manifests import AgentManifest, SkillDef

_FIXTURE_POLICY = os.path.join(os.path.dirname(__file__), 'fixtures', 'policy.csv')


def make_manifest(name: str, department: str, skill_id: str = 'invoice.verify', version: str = '1.0.0') -> AgentManifest:
    return AgentManifest(
        name=name, framework='langchain', department=department, version='1.0.0',
        endpoint='grpc://localhost:1', identity_ref='cert-1',
        skills=[SkillDef(
            skill_id=skill_id, version=version, side_effects='read_only',
            input_schema={'type': 'object'}, output_schema={'type': 'object'},
        )],
    )


@pytest.fixture
async def store():
    s = Store(':memory:')
    yield s
    await s.close()


@pytest.fixture
def policy():
    return PolicyEngine(secret_key='test-secret', policy_path=_FIXTURE_POLICY)


@pytest.fixture
def discovery(store, policy):
    return Discovery(store, policy)


async def test_find_skill_returns_authorized_provider(store, discovery):
    await store.upsert_agent(make_manifest('agent-a', department='finance'))
    results = await discovery.find_skill('finance', 'invoice.verify')
    assert [r.name for r in results] == ['agent-a']


async def test_find_skill_excludes_unauthorized_requester(store, discovery):
    await store.upsert_agent(make_manifest('agent-a', department='finance'))
    results = await discovery.find_skill('marketing', 'invoice.verify')
    assert results == []


async def test_find_skill_empty_when_no_providers(discovery):
    assert await discovery.find_skill('finance', 'invoice.verify') == []


async def test_find_skill_filters_by_version(store, discovery):
    await store.upsert_agent(make_manifest('agent-a', department='finance', version='1.0.0'))
    assert [r.name for r in await discovery.find_skill('finance', 'invoice.verify', version='1.0.0')] == ['agent-a']
    assert await discovery.find_skill('finance', 'invoice.verify', version='2.0.0') == []


async def test_describe_provider_returns_manifest(store, discovery):
    await store.upsert_agent(make_manifest('agent-a', department='finance'))
    described = await discovery.describe_provider('agent-a')
    assert described is not None
    assert described.name == 'agent-a'


async def test_describe_provider_returns_none_for_unknown(discovery):
    assert await discovery.describe_provider('nobody') is None
