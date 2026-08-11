import asyncio
import os

import grpc
from conftest import requires_nats
from google.protobuf.struct_pb2 import Struct

from conet.persistence.store import Store
from conet.protocols.grpc import skillruntime_pb2 as pb2
from conet.protocols.grpc import skillruntime_pb2_grpc as pb2_grpc
from conet.sdk.agent import Agent, start
from conet.sdk.manifests import SkillDef

pytestmark = requires_nats

_FIXTURE_POLICY = os.path.join(os.path.dirname(__file__), 'fixtures', 'policy_double_value.csv')


class FakeAdapter:
    def __init__(self, manifest):
        self._manifest = manifest

    def describe(self):
        return self._manifest

    async def invoke(self, skill_id: str, payload: dict) -> dict:
        return {'result': payload['value'] * 2}


def make_adapter(port: int, ttl: int = 30) -> FakeAdapter:
    manifest = Agent.manifest(
        name='doubler', framework='plain', department='finance',
        version='1.0.0', endpoint=f'grpc://localhost:{port}', identity_ref='cert-1',
        lease_ttl_seconds=ttl,
        skills=[SkillDef(
            skill_id='double.value', version='1.0.0', side_effects='read_only',
            input_schema={'type': 'object', 'properties': {'value': {'type': 'integer'}}, 'required': ['value']},
            output_schema={'type': 'object', 'properties': {'result': {'type': 'integer'}}, 'required': ['result']},
        )],
    )
    return FakeAdapter(manifest)


async def test_start_registers_the_agent_in_the_shared_store(tmp_path):
    db_path = str(tmp_path / 'conet.db')
    adapter = make_adapter(port=50171)

    running = await start(adapter, db_path=db_path, nats_url='nats://localhost:4222')
    try:
        other_view = Store(db_path)
        fetched = await other_view.get_agent('doubler')
        assert fetched is not None
        assert fetched.name == 'doubler'
        await other_view.close()
    finally:
        await running.stop()


async def test_started_agent_serves_real_grpc_calls(tmp_path):
    db_path = str(tmp_path / 'conet.db')
    adapter = make_adapter(port=50172)

    running = await start(adapter, db_path=db_path, nats_url='nats://localhost:4222', policy_path=_FIXTURE_POLICY)
    try:
        from conet.control.policy import PolicyEngine
        policy = PolicyEngine(secret_key='dev-secret-change-me')
        token = policy.mint_auth_context('finance', 'double.value')

        channel = grpc.aio.insecure_channel('localhost:50172')
        stub = pb2_grpc.SkillRuntimeStub(channel)
        payload = Struct()
        payload.update({'value': 10})
        resp = await stub.Execute(pb2.SkillRequest(
            skill_id='double.value', task_id='t1', auth_context=token, input=payload,
        ))
        await channel.close()

        assert resp.status == pb2.OK
        assert dict(resp.output) == {'result': 20}
    finally:
        await running.stop()


async def test_stop_unregisters_the_agent(tmp_path):
    db_path = str(tmp_path / 'conet.db')
    adapter = make_adapter(port=50173)

    running = await start(adapter, db_path=db_path, nats_url='nats://localhost:4222')
    await running.stop()

    view = Store(db_path)
    assert await view.get_agent('doubler') is None
    await view.close()


async def test_lease_renewal_keeps_the_agent_active_past_its_original_ttl(tmp_path):
    db_path = str(tmp_path / 'conet.db')
    adapter = make_adapter(port=50174, ttl=1)  # renews every 0.5s

    running = await start(adapter, db_path=db_path, nats_url='nats://localhost:4222')
    try:
        await asyncio.sleep(1.5)  # longer than the original 1s ttl
        view = Store(db_path)
        assert await view.is_agent_active('doubler') is True
        await view.close()
    finally:
        await running.stop()
