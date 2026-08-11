"""SRS §10 acceptance test for v0.1 — run two agents on separate processes:

  - Agent B registers Skill math.add.
  - Agent A discovers it without a hard-coded endpoint.
  - Agent A receives authorization and executes via gRPC, receiving a result.
  - A policy denial is demonstrated (unauthorized call refused and audited).
  - An administrator cancels an active task without affecting unrelated tasks.
  - Agent B becomes undiscoverable after lease expiry.
  - A single trace spans the request end to end, and an audit record
    explains what happened.
"""
import asyncio
import multiprocessing
import secrets

import grpc
import pytest
from conftest import requires_nats
from google.protobuf.struct_pb2 import Struct

from conet.control.discovery import Discovery
from conet.control.policy import PolicyEngine
from conet.persistence.store import Store
from conet.protocols.grpc import skillruntime_pb2 as pb2
from conet.protocols.grpc import skillruntime_pb2_grpc as pb2_grpc
from conet.sdk.agent import Agent
from conet.sdk.manifests import SkillDef

pytestmark = requires_nats

AGENT_B_PORT = 50199
SKILL_ID = 'math.add'


class MathAdapter:
    """Agent B's adapter — module-level so it's picklable for multiprocessing.Process."""

    def describe(self):
        return Agent.manifest(
            name='agent-b', framework='plain', department='finance', version='1.0.0',
            endpoint=f'grpc://localhost:{AGENT_B_PORT}', identity_ref='cert-b',
            lease_ttl_seconds=3,
            skills=[SkillDef(
                skill_id=SKILL_ID, version='1.0.0', side_effects='read_only',
                input_schema={
                    'type': 'object',
                    'properties': {'a': {'type': 'integer'}, 'b': {'type': 'integer'}},
                    'required': ['a', 'b'],
                },
                output_schema={'type': 'object', 'properties': {'sum': {'type': 'integer'}}, 'required': ['sum']},
            )],
        )

    async def invoke(self, skill_id: str, payload: dict) -> dict:
        if payload.get('slow'):
            await asyncio.sleep(30)  # never completes on its own; must be cancelled
        return {'sum': payload['a'] + payload['b']}


def _run_agent_b(db_path: str, nats_url: str, policy_path: str) -> None:
    from conet.sdk.agent import run
    run(MathAdapter(), db_path=db_path, nats_url=nats_url, policy_path=policy_path)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / 'conet.db')


@pytest.fixture
def policy_path(tmp_path):
    path = tmp_path / 'policy.csv'
    path.write_text('p, finance, math.add, invoke\n')
    return str(path)


async def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.1) -> bool:
    elapsed = 0.0
    while elapsed < timeout:
        if await predicate():
            return True
        await asyncio.sleep(interval)
        elapsed += interval
    return False


async def test_srs10_acceptance(db_path, policy_path):
    agent_b_process = multiprocessing.Process(
        target=_run_agent_b, args=(db_path, 'nats://localhost:4222', policy_path), daemon=True,
    )
    agent_b_process.start()
    store = Store(db_path)
    policy = PolicyEngine(secret_key='dev-secret-change-me', policy_path=policy_path)
    discovery = Discovery(store, policy)

    try:
        # --- Agent B registers Skill math.add ---
        registered = await _wait_until(lambda: _agent_exists(store))
        assert registered, 'Agent B did not register within the timeout'

        # --- Agent A discovers it without a hard-coded endpoint ---
        providers = await discovery.find_skill('finance', SKILL_ID)
        assert len(providers) == 1
        endpoint = providers[0].endpoint
        assert endpoint == f'grpc://localhost:{AGENT_B_PORT}'  # came from discovery, not a literal in Agent A's code

        # --- Agent A is authorized and executes over gRPC ---
        trace_id = secrets.token_hex(16)
        token = policy.mint_auth_context('finance', SKILL_ID)
        channel = grpc.aio.insecure_channel(endpoint.split('://', 1)[-1])
        stub = pb2_grpc.SkillRuntimeStub(channel)

        add_input = Struct()
        add_input.update({'a': 2, 'b': 3})
        resp = await stub.Execute(pb2.SkillRequest(
            skill_id=SKILL_ID, task_id='acc-add', trace_id=trace_id, auth_context=token, input=add_input,
        ))
        assert resp.status == pb2.OK
        assert dict(resp.output) == {'sum': 5}

        # --- A policy denial is demonstrated (unauthorized call refused and audited) ---
        denied = await discovery.find_skill('marketing', SKILL_ID)
        assert denied == [], 'marketing has no policy rule for math.add and must be excluded'
        denial_audit = [e for e in await store.list_audit_events() if e.outcome == 'DENIED' and e.actor == 'marketing']
        assert denial_audit, 'the discovery denial must leave an audit record (FR-022)'

        # --- An administrator cancels an active task without affecting unrelated tasks ---
        slow_input = Struct()
        slow_input.update({'a': 1, 'b': 1, 'slow': True})
        slow_call = stub.Execute(pb2.SkillRequest(
            skill_id=SKILL_ID, task_id='acc-cancel-me', auth_context=token, input=slow_input,
        ))
        await asyncio.sleep(0.3)  # let it register as RUNNING before cancelling

        # an unrelated task, executed while the slow one is in flight
        unrelated_input = Struct()
        unrelated_input.update({'a': 10, 'b': 20})
        unrelated_resp = await stub.Execute(pb2.SkillRequest(
            skill_id=SKILL_ID, task_id='acc-unrelated', auth_context=token, input=unrelated_input,
        ))
        assert unrelated_resp.status == pb2.OK
        assert dict(unrelated_resp.output) == {'sum': 30}, 'the unrelated task must complete normally'

        ack = await stub.Cancel(pb2.CancelRequest(task_id='acc-cancel-me'))
        assert ack.acknowledged is True
        slow_resp = await slow_call
        assert slow_resp.status == pb2.CANCELLED

        await channel.close()

        # --- Agent B becomes undiscoverable after lease expiry ---
        agent_b_process.terminate()  # simulate a crash: its renew loop stops
        agent_b_process.join(timeout=5)
        gone = await _wait_until(lambda: _agent_gone(discovery), timeout=6)
        assert gone, 'Agent B must disappear from discovery once its lease (ttl=3s) is not renewed'

        # --- A single trace spans the request end to end, and an audit record explains what happened ---
        trace_audit = await store.list_audit_events(trace_id=trace_id)
        assert len(trace_audit) == 1
        assert trace_audit[0].outcome == 'OK'
        assert trace_audit[0].resource == SKILL_ID
        completed_task = await store.get_task('acc-add')
        assert completed_task.trace_id == trace_id
        assert completed_task.state == 'COMPLETED'

    finally:
        if agent_b_process.is_alive():
            agent_b_process.terminate()
            agent_b_process.join(timeout=5)
        await store.close()


async def _agent_exists(store: Store) -> bool:
    return await store.get_agent('agent-b') is not None


async def _agent_gone(discovery: Discovery) -> bool:
    return await discovery.find_skill('finance', SKILL_ID) == []
