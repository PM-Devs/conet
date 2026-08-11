import asyncio
import os

import grpc
import pytest
from google.protobuf.struct_pb2 import Struct

from conet.control.approvals import ApprovalWorkflow
from conet.control.policy import PolicyEngine
from conet.persistence.store import Store
from conet.protocols.grpc import skillruntime_pb2 as pb2
from conet.protocols.grpc import skillruntime_pb2_grpc as pb2_grpc
from conet.runtime.server import serve
from conet.sdk.manifests import AgentManifest, SkillDef

SKILL_ID = 'finance.issue_refund'
_FIXTURE_POLICY = os.path.join(os.path.dirname(__file__), 'fixtures', 'policy_refund.csv')
_APPROVER = 'approver@example.com'


class CountingAdapter:
    def __init__(self):
        self.invoke_count = 0

    async def invoke(self, skill_id: str, payload: dict) -> dict:
        self.invoke_count += 1
        return {'refunded': True}


def make_manifest() -> AgentManifest:
    return AgentManifest(
        name='finance-agent', framework='plain', department='finance', version='1.0.0',
        endpoint='grpc://localhost:1', identity_ref='cert-finance',
        skills=[SkillDef(
            skill_id=SKILL_ID, version='1.0.0', side_effects='unsafe_write',
            input_schema={'type': 'object', 'properties': {'invoice_id': {'type': 'string'}}, 'required': ['invoice_id']},
            output_schema={'type': 'object', 'properties': {'refunded': {'type': 'boolean'}}},
        )],
    )


@pytest.fixture
def policy():
    return PolicyEngine(secret_key='test-secret', policy_path=_FIXTURE_POLICY)


@pytest.fixture
def adapter():
    return CountingAdapter()


@pytest.fixture
async def store(tmp_path):
    s = Store(str(tmp_path / 'conet.db'))
    yield s
    await s.close()


def to_struct(d: dict) -> Struct:
    s = Struct()
    s.update(d)
    return s


def make_request(policy: PolicyEngine, task_id: str) -> pb2.SkillRequest:
    token = policy.mint_auth_context('finance', SKILL_ID)
    return pb2.SkillRequest(skill_id=SKILL_ID, task_id=task_id, auth_context=token, input=to_struct({'invoice_id': 'INV-1'}))


async def _wait_for_pending_approval(store: Store, task_id: str, timeout: float = 5.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        for approval in await store.list_pending_approvals():
            if approval.task_id == task_id:
                return approval
        await asyncio.sleep(0.02)
    raise TimeoutError(f'no pending approval appeared for task_id={task_id!r}')


async def test_unsafe_write_executes_immediately_when_ungated(policy, adapter, store):
    grpc_server = await serve(make_manifest(), adapter, policy, port=50170, store=store)
    channel = grpc.aio.insecure_channel('localhost:50170')
    stub = pb2_grpc.SkillRuntimeStub(channel)
    try:
        resp = await stub.Execute(make_request(policy, 't-ungated'))
    finally:
        await channel.close()
        await grpc_server.stop(None)

    assert resp.status == pb2.OK
    assert adapter.invoke_count == 1


async def test_unsafe_write_waits_for_approval_then_proceeds(policy, adapter, store):
    approvals = ApprovalWorkflow(store)
    grpc_server = await serve(
        make_manifest(), adapter, policy, port=50171, store=store,
        approvals=approvals, approvers=[_APPROVER], approval_poll_interval_seconds=0.05,
    )
    channel = grpc.aio.insecure_channel('localhost:50171')
    stub = pb2_grpc.SkillRuntimeStub(channel)
    try:
        execute_task = asyncio.ensure_future(stub.Execute(make_request(policy, 't-approve')))
        approval = await _wait_for_pending_approval(store, 't-approve')
        assert adapter.invoke_count == 0  # still waiting, adapter must not have run yet

        await approvals.approve(approval.approval_id, decided_by=_APPROVER)
        resp = await execute_task
    finally:
        await channel.close()
        await grpc_server.stop(None)

    assert resp.status == pb2.OK
    assert adapter.invoke_count == 1


async def test_unsafe_write_rejected_never_reaches_the_adapter(policy, adapter, store):
    approvals = ApprovalWorkflow(store)
    grpc_server = await serve(
        make_manifest(), adapter, policy, port=50172, store=store,
        approvals=approvals, approvers=[_APPROVER], approval_poll_interval_seconds=0.05,
    )
    channel = grpc.aio.insecure_channel('localhost:50172')
    stub = pb2_grpc.SkillRuntimeStub(channel)
    try:
        execute_task = asyncio.ensure_future(stub.Execute(make_request(policy, 't-reject')))
        approval = await _wait_for_pending_approval(store, 't-reject')

        await approvals.reject(approval.approval_id, decided_by=_APPROVER)
        resp = await execute_task
    finally:
        await channel.close()
        await grpc_server.stop(None)

    assert resp.status == pb2.DENIED
    assert adapter.invoke_count == 0


async def test_unsafe_write_times_out_without_a_decision(policy, adapter, store):
    approvals = ApprovalWorkflow(store)
    grpc_server = await serve(
        make_manifest(), adapter, policy, port=50173, store=store,
        approvals=approvals, approvers=[_APPROVER],
        approval_ttl_seconds=1, approval_poll_interval_seconds=0.1,
    )
    channel = grpc.aio.insecure_channel('localhost:50173')
    stub = pb2_grpc.SkillRuntimeStub(channel)
    try:
        resp = await stub.Execute(make_request(policy, 't-expire'))
    finally:
        await channel.close()
        await grpc_server.stop(None)

    assert resp.status == pb2.TIMED_OUT
    assert adapter.invoke_count == 0


async def test_read_only_skill_is_never_gated_even_with_approvers_configured(policy, store):
    manifest = AgentManifest(
        name='finance-agent', framework='plain', department='finance', version='1.0.0',
        endpoint='grpc://localhost:1', identity_ref='cert-finance',
        skills=[SkillDef(
            skill_id='finance.check_balance', version='1.0.0', side_effects='read_only',
            input_schema={'type': 'object'}, output_schema={'type': 'object'},
        )],
    )
    read_only_policy = PolicyEngine(secret_key='test-secret')
    read_only_policy.add_policy_rule('finance', 'finance.check_balance', 'invoke')

    class ReadOnlyAdapter:
        async def invoke(self, skill_id, payload):
            return {}

    approvals = ApprovalWorkflow(store)
    grpc_server = await serve(
        manifest, ReadOnlyAdapter(), read_only_policy, port=50174, store=store,
        approvals=approvals, approvers=[_APPROVER],
    )
    channel = grpc.aio.insecure_channel('localhost:50174')
    stub = pb2_grpc.SkillRuntimeStub(channel)
    try:
        token = read_only_policy.mint_auth_context('finance', 'finance.check_balance')
        req = pb2.SkillRequest(skill_id='finance.check_balance', task_id='t-read-only', auth_context=token, input=to_struct({}))
        resp = await stub.Execute(req)
    finally:
        await channel.close()
        await grpc_server.stop(None)

    assert resp.status == pb2.OK
