import asyncio
import os

import grpc
import pytest
from google.protobuf.struct_pb2 import Struct
from grpc_health.v1 import health_pb2, health_pb2_grpc

from conet.control.policy import PolicyEngine
from conet.persistence.store import Store
from conet.protocols.grpc import skillruntime_pb2 as pb2
from conet.protocols.grpc import skillruntime_pb2_grpc as pb2_grpc
from conet.runtime.server import serve
from conet.sdk.manifests import AgentManifest, SkillDef

SKILL_ID = 'double.value'
_FIXTURE_POLICY = os.path.join(os.path.dirname(__file__), 'fixtures', 'policy_double_value.csv')


class FakeAdapter:
    def __init__(self):
        self.saw_cancellation = False

    async def invoke(self, skill_id: str, payload: dict) -> dict:
        if payload.get('slow'):
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                self.saw_cancellation = True
                raise
        if payload.get('boom'):
            raise RuntimeError('boom')
        if payload.get('bad_output'):
            return {'result': 'not-an-integer'}
        return {'result': payload['value'] * 2}

    async def stream(self, skill_id: str, payload: dict):
        for i in range(3):
            await asyncio.sleep(0.05)
            yield {'seq': i}


def make_manifest() -> AgentManifest:
    return AgentManifest(
        name='doubler', framework='plain', department='finance', version='1.0.0',
        endpoint='grpc://localhost:1', identity_ref='cert-1',
        skills=[SkillDef(
            skill_id=SKILL_ID, version='1.0.0', side_effects='read_only', execution_modes=['unary', 'stream'],
            input_schema={'type': 'object', 'properties': {'value': {'type': 'integer'}}, 'required': ['value']},
            output_schema={'type': 'object', 'properties': {'result': {'type': 'integer'}}, 'required': ['result']},
        )],
    )


@pytest.fixture
def policy():
    return PolicyEngine(secret_key='test-secret', policy_path=_FIXTURE_POLICY)


@pytest.fixture
def adapter():
    return FakeAdapter()


@pytest.fixture
async def running_server(policy, adapter):
    grpc_server = await serve(make_manifest(), adapter, policy, port=50161)
    channel = grpc.aio.insecure_channel('localhost:50161')
    stub = pb2_grpc.SkillRuntimeStub(channel)
    yield stub
    await channel.close()
    await grpc_server.stop(None)


def to_struct(d: dict) -> Struct:
    s = Struct()
    s.update(d)
    return s


def request(policy: PolicyEngine, task_id: str, payload: dict, skill_id: str = SKILL_ID, bad_token: bool = False) -> pb2.SkillRequest:
    token = 'garbage' if bad_token else policy.mint_auth_context('finance', skill_id)
    return pb2.SkillRequest(skill_id=skill_id, task_id=task_id, auth_context=token, input=to_struct(payload))


async def test_execute_succeeds_with_valid_auth_and_input(running_server, policy):
    resp = await running_server.Execute(request(policy, 't1', {'value': 21}))
    assert resp.status == pb2.OK
    assert dict(resp.output) == {'result': 42}


async def test_execute_denied_with_invalid_token(running_server, policy):
    resp = await running_server.Execute(request(policy, 't2', {'value': 1}, bad_token=True))
    assert resp.status == pb2.DENIED


async def test_execute_denied_for_a_validly_signed_token_of_an_unauthorized_subject(running_server, policy):
    # 'marketing' has no policy rule for double.value in the fixture, but the
    # token itself is validly signed -- proves Execute re-checks authorize(),
    # not just the token signature (FR-014).
    token = policy.mint_auth_context('marketing', SKILL_ID)
    req = pb2.SkillRequest(skill_id=SKILL_ID, task_id='t-unauth', auth_context=token, input=to_struct({'value': 1}))
    resp = await running_server.Execute(req)
    assert resp.status == pb2.DENIED


async def test_execute_denied_when_token_skill_id_mismatches_request(running_server, policy):
    token = policy.mint_auth_context('finance', 'some.other.skill')
    req = pb2.SkillRequest(skill_id=SKILL_ID, task_id='t3', auth_context=token, input=to_struct({'value': 1}))
    resp = await running_server.Execute(req)
    assert resp.status == pb2.DENIED


async def test_execute_denial_audit_names_the_real_subject_not_unknown(policy, adapter, tmp_path):
    # a validly-signed token for an unauthorized subject must still be
    # attributed to that subject in the audit trail, not 'unknown' --
    # claims were already verified by the time authorize() said no.
    store = Store(str(tmp_path / 'conet.db'))
    grpc_server = await serve(make_manifest(), adapter, policy, port=50162, store=store)
    channel = grpc.aio.insecure_channel('localhost:50162')
    stub = pb2_grpc.SkillRuntimeStub(channel)
    try:
        token = policy.mint_auth_context('marketing', SKILL_ID)
        req = pb2.SkillRequest(skill_id=SKILL_ID, task_id='t-audit-denied', auth_context=token, input=to_struct({'value': 1}))
        resp = await stub.Execute(req)
        assert resp.status == pb2.DENIED
    finally:
        await channel.close()
        await grpc_server.stop(None)

    events = await store.list_audit_events()
    await store.close()
    assert any(e.actor == 'marketing' and e.outcome == 'DENIED' for e in events)


async def test_execute_failed_on_bad_input_never_reaches_adapter(running_server, policy):
    resp = await running_server.Execute(request(policy, 't4', {'value': 'not-an-int'}))
    assert resp.status == pb2.FAILED
    assert 'input validation failed' in resp.error_detail


async def test_execute_failed_when_adapter_raises(running_server, policy):
    resp = await running_server.Execute(request(policy, 't5', {'value': 1, 'boom': True}))
    assert resp.status == pb2.FAILED


async def test_execute_failed_on_bad_output(running_server, policy):
    resp = await running_server.Execute(request(policy, 't6', {'value': 1, 'bad_output': True}))
    assert resp.status == pb2.FAILED
    assert 'output validation failed' in resp.error_detail


async def test_execute_stream_yields_three_chunks(running_server, policy):
    chunks = [c async for c in running_server.ExecuteStream(request(policy, 't7', {'value': 1}))]
    assert [c.seq for c in chunks] == [0, 1, 2]


async def test_cancel_stops_a_running_task(running_server, policy, adapter):
    call = running_server.Execute(request(policy, 't8', {'value': 1, 'slow': True}))
    await asyncio.sleep(0.2)  # let Execute register the task before cancelling
    ack = await running_server.Cancel(pb2.CancelRequest(task_id='t8'))
    assert ack.acknowledged is True

    resp = await call
    assert resp.status == pb2.CANCELLED
    assert adapter.saw_cancellation is True


async def test_cancel_unknown_task_is_not_acknowledged(running_server):
    ack = await running_server.Cancel(pb2.CancelRequest(task_id='does-not-exist'))
    assert ack.acknowledged is False


async def test_serve_exposes_grpc_health_checking(running_server):
    # running_server is a SkillRuntimeStub; open a second stub on the same
    # channel's target for the standard grpc.health.v1.Health service.
    channel = grpc.aio.insecure_channel('localhost:50161')
    try:
        health_stub = health_pb2_grpc.HealthStub(channel)
        overall = await health_stub.Check(health_pb2.HealthCheckRequest(service=''))
        assert overall.status == health_pb2.HealthCheckResponse.SERVING
        skill_runtime = await health_stub.Check(health_pb2.HealthCheckRequest(service='conet.runtime.SkillRuntime'))
        assert skill_runtime.status == health_pb2.HealthCheckResponse.SERVING
    finally:
        await channel.close()


async def test_execute_persists_a_completed_task_when_store_is_given(tmp_path, policy, adapter):
    store = Store(str(tmp_path / 'conet.db'))
    grpc_server = await serve(make_manifest(), adapter, policy, port=50162, store=store)
    channel = grpc.aio.insecure_channel('localhost:50162')
    stub = pb2_grpc.SkillRuntimeStub(channel)
    try:
        await stub.Execute(request(policy, 'task-99', {'value': 5}))
        task = await store.get_task('task-99')
        assert task is not None
        assert task.state == 'COMPLETED'
        assert task.requester == 'finance'
        assert task.provider == 'doubler'
    finally:
        await channel.close()
        await grpc_server.stop(None)
        await store.close()
