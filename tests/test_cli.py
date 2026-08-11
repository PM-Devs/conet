import asyncio
import importlib
import os

import pytest

from conet.control.approvals import ApprovalWorkflow
from conet.control.policy import PolicyEngine
from conet.persistence.store import Store
from conet.runtime.server import serve
from conet.sdk.manifests import AgentManifest, SkillDef, Task

_FIXTURE_POLICY = os.path.join(os.path.dirname(__file__), 'fixtures', 'policy_double_value.csv')

# conet.cli's __init__.py does `from .main import main`, which rebinds the
# `main` attribute on the package to the *function* (required for the
# `conet.cli:main` console-script entry point) -- so `conet.cli.main` no
# longer resolves to the submodule via attribute access. Fetch it directly.
cli = importlib.import_module('conet.cli.main')


def make_manifest(name: str, port: int) -> AgentManifest:
    return AgentManifest(
        name=name, framework='plain', department='finance', version='1.0.0',
        endpoint=f'grpc://localhost:{port}', identity_ref='cert-1',
        skills=[SkillDef(
            skill_id='double.value', version='1.0.0', side_effects='read_only',
            input_schema={'type': 'object'}, output_schema={'type': 'object'},
        )],
    )


@pytest.fixture(autouse=True)
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / 'conet.db')
    monkeypatch.setenv('CONET_DB_PATH', path)
    return path


async def test_status_reports_zero_agents_on_empty_store(db_path, capsys):
    await cli._status()
    out = capsys.readouterr().out
    assert 'healthy' in out
    assert 'Active agents: 0' in out


async def test_agents_lists_registered_agents(db_path, capsys):
    store = Store(db_path)
    await store.upsert_agent(make_manifest('agent-a', 50181))
    await store.close()

    await cli._agents()
    out = capsys.readouterr().out
    assert 'agent-a' in out
    assert 'dept=finance' in out


async def test_agents_prints_message_when_none_registered(db_path, capsys):
    await cli._agents()
    assert 'No agents registered' in capsys.readouterr().out


async def test_skills_lists_published_skills(db_path, capsys):
    store = Store(db_path)
    await store.upsert_agent(make_manifest('agent-a', 50182))
    await store.close()

    await cli._skills()
    out = capsys.readouterr().out
    assert 'double.value' in out
    assert 'provider=agent-a' in out


async def test_cancel_reports_unknown_task(db_path, capsys):
    await cli._cancel('does-not-exist')
    assert 'No such task' in capsys.readouterr().out


async def test_cancel_reaches_the_real_provider_and_stops_the_task(db_path, capsys):
    policy = PolicyEngine(secret_key='dev-secret-change-me', policy_path=_FIXTURE_POLICY)
    manifest = make_manifest('agent-a', 50183)

    class SlowAdapter:
        async def invoke(self, skill_id, payload):
            await asyncio.sleep(5)

    store = Store(db_path)
    await store.upsert_agent(manifest)  # simulate the agent having already registered itself
    grpc_server = await serve(manifest, SlowAdapter(), policy, port=50183, store=store)

    import grpc as grpc_lib
    from google.protobuf.struct_pb2 import Struct

    from conet.protocols.grpc import skillruntime_pb2 as pb2
    from conet.protocols.grpc import skillruntime_pb2_grpc as pb2_grpc

    token = policy.mint_auth_context('finance', 'double.value')
    channel = grpc_lib.aio.insecure_channel('localhost:50183')
    stub = pb2_grpc.SkillRuntimeStub(channel)
    call = stub.Execute(pb2.SkillRequest(skill_id='double.value', task_id='cli-t1', auth_context=token, input=Struct()))
    await asyncio.sleep(0.2)  # let Execute register the task and save it

    await cli._cancel('cli-t1')
    out = capsys.readouterr().out
    assert 'cancelled' in out

    resp = await call
    assert resp.status == pb2.CANCELLED

    await channel.close()
    await grpc_server.stop(None)
    await store.close()


async def test_approvals_reports_none_pending(db_path, capsys):
    await cli._approvals()
    assert 'No pending approvals' in capsys.readouterr().out


async def test_approvals_lists_a_pending_approval(db_path, capsys):
    store = Store(db_path)
    await store.save_task(Task(task_id='t-approvals', requester='finance-agent', skill_id='finance.issue_refund'))
    workflow = ApprovalWorkflow(store)
    await workflow.request_approval('t-approvals', approvers=['approver@example.com'])
    await store.close()

    await cli._approvals()
    out = capsys.readouterr().out
    assert 'task=t-approvals' in out
    assert 'approver@example.com' in out


async def test_approve_reports_unknown_approval(db_path, capsys):
    await cli._decide('does-not-exist', 'approver@example.com', approve=True)
    assert 'No such approval' in capsys.readouterr().out


async def test_approve_unblocks_the_task(db_path, capsys):
    store = Store(db_path)
    await store.save_task(Task(task_id='t-cli-approve', requester='finance-agent', skill_id='finance.issue_refund'))
    workflow = ApprovalWorkflow(store)
    approval = await workflow.request_approval('t-cli-approve', approvers=['approver@example.com'])
    await store.close()

    await cli._decide(approval.approval_id, 'approver@example.com', approve=True)
    assert 'approved by approver@example.com' in capsys.readouterr().out

    store = Store(db_path)
    task = await store.get_task('t-cli-approve')
    await store.close()
    assert task.state == 'ROUTING'


async def test_reject_marks_the_task_rejected(db_path, capsys):
    store = Store(db_path)
    await store.save_task(Task(task_id='t-cli-reject', requester='finance-agent', skill_id='finance.issue_refund'))
    workflow = ApprovalWorkflow(store)
    approval = await workflow.request_approval('t-cli-reject', approvers=['approver@example.com'])
    await store.close()

    await cli._decide(approval.approval_id, 'approver@example.com', approve=False)
    assert 'rejected by approver@example.com' in capsys.readouterr().out

    store = Store(db_path)
    task = await store.get_task('t-cli-reject')
    await store.close()
    assert task.state == 'REJECTED'
