import os

import pytest

from conet.control.discovery import Discovery
from conet.control.policy import PolicyEngine
from conet.persistence.store import Store
from conet.protocols.grpc import skillruntime_pb2 as pb2
from conet.runtime.router import NoProviderAvailableError, Router
from conet.runtime.server import serve
from conet.sdk.manifests import AgentManifest, SkillDef

_FIXTURE_POLICY = os.path.join(os.path.dirname(__file__), 'fixtures', 'policy_double_value.csv')


class FlakyAdapter:
    """Fails every call; used to prove failover moves on to the next provider."""

    async def invoke(self, skill_id, payload):
        raise RuntimeError('this provider is broken')


class WorkingAdapter:
    def __init__(self, marker: str):
        self._marker = marker

    async def invoke(self, skill_id, payload):
        return {'result': payload['value'] * 2, 'served_by': self._marker}


def make_manifest(name: str, port: int, side_effects: str = 'read_only') -> AgentManifest:
    return AgentManifest(
        name=name, framework='plain', department='finance', version='1.0.0',
        endpoint=f'grpc://localhost:{port}', identity_ref='cert-1',
        skills=[SkillDef(
            skill_id='double.value', version='1.0.0', side_effects=side_effects,
            input_schema={'type': 'object', 'properties': {'value': {'type': 'integer'}}, 'required': ['value']},
            output_schema={'type': 'object'},
        )],
    )


@pytest.fixture
def policy():
    return PolicyEngine(secret_key='test-secret', policy_path=_FIXTURE_POLICY)


@pytest.fixture
async def store():
    s = Store(':memory:')
    yield s
    await s.close()


@pytest.fixture
def router(store, policy):
    return Router(Discovery(store, policy), policy)


async def test_select_providers_is_deterministically_sorted(store, router):
    await store.upsert_agent(make_manifest('zebra', 50210))
    await store.upsert_agent(make_manifest('alpha', 50211))
    providers = await router.select_providers('finance', 'double.value')
    assert [p.name for p in providers] == ['alpha', 'zebra']


async def test_execute_raises_when_no_provider_available(router):
    with pytest.raises(NoProviderAvailableError):
        await router.execute('finance', 'double.value', {'value': 1})


async def test_execute_succeeds_with_a_single_healthy_provider(store, router):
    await store.upsert_agent(make_manifest('agent-a', 50212))
    grpc_server = await serve(make_manifest('agent-a', 50212), WorkingAdapter('a'), PolicyEngine(secret_key='test-secret', policy_path=_FIXTURE_POLICY), port=50212)
    try:
        resp = await router.execute('finance', 'double.value', {'value': 5})
        assert resp.status == pb2.OK
        assert dict(resp.output)['result'] == 10
    finally:
        await grpc_server.stop(None)


async def test_execute_does_not_retry_a_non_read_only_skill(store, router):
    """unsafe_write must never be auto-retried (FR-012, FR-013): agent-a's
    adapter fails, and agent-b (sorted second, never given a running
    server) must never be touched -- if the router incorrectly tried it,
    the connection failure would surface as a raised AioRpcError instead
    of the FAILED response asserted below."""
    await store.upsert_agent(make_manifest('agent-a', 50213, side_effects='unsafe_write'))
    await store.upsert_agent(make_manifest('agent-b', 50214, side_effects='unsafe_write'))
    broken_server = await serve(
        make_manifest('agent-a', 50213, side_effects='unsafe_write'), FlakyAdapter(),
        PolicyEngine(secret_key='test-secret', policy_path=_FIXTURE_POLICY), port=50213,
    )
    try:
        resp = await router.execute('finance', 'double.value', {'value': 1})
        assert resp.status == pb2.FAILED
    finally:
        await broken_server.stop(None)


async def test_execute_fails_over_to_next_provider_for_a_read_only_skill(store, router):
    await store.upsert_agent(make_manifest('agent-a', 50215))  # sorts first; will be unreachable
    await store.upsert_agent(make_manifest('agent-b', 50216))  # sorts second; healthy

    working_server = await serve(
        make_manifest('agent-b', 50216), WorkingAdapter('b'),
        PolicyEngine(secret_key='test-secret', policy_path=_FIXTURE_POLICY), port=50216,
    )
    try:
        # agent-a has no server listening on 50215 at all -> connection failure -> failover to agent-b
        resp = await router.execute('finance', 'double.value', {'value': 7})
        assert resp.status == pb2.OK
        assert dict(resp.output) == {'result': 14, 'served_by': 'b'}
    finally:
        await working_server.stop(None)
