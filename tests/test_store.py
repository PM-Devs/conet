from datetime import datetime, timedelta, timezone

import pytest

from conet.persistence.store import Store
from conet.sdk.manifests import AgentManifest, Approval, AuditEvent, SkillDef, Task


def make_manifest(name: str, skill_id: str = 'invoice.verify', ttl: int = 30) -> AgentManifest:
    return AgentManifest(
        name=name, framework='langchain', department='finance', version='1.0.0',
        endpoint='grpc://localhost:1', identity_ref='cert-1', lease_ttl_seconds=ttl,
        skills=[SkillDef(
            skill_id=skill_id, version='1.0.0', side_effects='read_only',
            input_schema={'type': 'object'}, output_schema={'type': 'object'},
        )],
    )


@pytest.fixture
async def store():
    s = Store(':memory:')
    yield s
    await s.close()


async def test_upsert_and_get_agent(store):
    await store.upsert_agent(make_manifest('agent-a'))
    fetched = await store.get_agent('agent-a')
    assert fetched is not None
    assert fetched.name == 'agent-a'
    assert fetched.skills[0].skill_id == 'invoice.verify'


async def test_get_agent_missing_returns_none(store):
    assert await store.get_agent('does-not-exist') is None


async def test_list_active_providers_filters_by_skill(store):
    await store.upsert_agent(make_manifest('agent-a', skill_id='invoice.verify'))
    await store.upsert_agent(make_manifest('agent-b', skill_id='research.find_leads'))

    providers = await store.list_active_providers('invoice.verify')
    assert [p.name for p in providers] == ['agent-a']


async def test_list_active_providers_excludes_expired_lease(store):
    await store.upsert_agent(make_manifest('agent-a', ttl=-10))  # already expired
    providers = await store.list_active_providers('invoice.verify')
    assert providers == []


async def test_upsert_agent_replaces_skills_on_re_registration(store):
    await store.upsert_agent(make_manifest('agent-a', skill_id='invoice.verify'))
    await store.upsert_agent(make_manifest('agent-a', skill_id='invoice.audit'))

    assert await store.list_active_providers('invoice.verify') == []
    providers = await store.list_active_providers('invoice.audit')
    assert [p.name for p in providers] == ['agent-a']


async def test_is_agent_active_true_for_live_lease(store):
    await store.upsert_agent(make_manifest('agent-a', ttl=30))
    assert await store.is_agent_active('agent-a') is True


async def test_is_agent_active_false_for_expired_lease(store):
    await store.upsert_agent(make_manifest('agent-a', ttl=-10))
    assert await store.is_agent_active('agent-a') is False


async def test_is_agent_active_false_for_unknown_agent(store):
    assert await store.is_agent_active('nobody') is False


async def test_deactivate_agent_removes_it(store):
    await store.upsert_agent(make_manifest('agent-a'))
    await store.deactivate_agent('agent-a')
    assert await store.get_agent('agent-a') is None
    assert await store.list_active_providers('invoice.verify') == []


async def test_deactivate_agent_on_unknown_agent_does_not_raise(store):
    await store.deactivate_agent('nobody')


async def test_save_task_roundtrip(store):
    task = Task(requester='agent-a', skill_id='invoice.verify')
    await store.save_task(task)
    task.state = 'RUNNING'
    await store.save_task(task)  # update path should not raise


async def test_get_task_returns_saved_task(store):
    task = Task(requester='agent-a', provider='agent-b', skill_id='invoice.verify')
    await store.save_task(task)
    fetched = await store.get_task(task.task_id)
    assert fetched is not None
    assert fetched.provider == 'agent-b'
    assert fetched.requester == 'agent-a'


async def test_get_task_reflects_latest_state(store):
    task = Task(requester='agent-a', skill_id='invoice.verify')
    await store.save_task(task)
    task.state = 'COMPLETED'
    await store.save_task(task)
    fetched = await store.get_task(task.task_id)
    assert fetched.state == 'COMPLETED'


async def test_get_task_returns_none_for_unknown_task(store):
    assert await store.get_task('does-not-exist') is None


async def test_list_recent_tasks_orders_newest_first(store):
    import asyncio
    t1 = Task(task_id='t1', requester='agent-a', skill_id='invoice.verify')
    await store.save_task(t1)
    await asyncio.sleep(0.01)
    t2 = Task(task_id='t2', requester='agent-a', skill_id='invoice.verify')
    await store.save_task(t2)
    tasks = await store.list_recent_tasks()
    assert [t.task_id for t in tasks] == ['t2', 't1']


async def test_list_recent_tasks_respects_limit(store):
    for i in range(5):
        await store.save_task(Task(task_id=f't{i}', requester='agent-a', skill_id='invoice.verify'))
    tasks = await store.list_recent_tasks(limit=2)
    assert len(tasks) == 2


async def test_list_all_agents_returns_only_active(store):
    await store.upsert_agent(make_manifest('agent-a'))
    await store.upsert_agent(make_manifest('agent-b', ttl=-10))
    names = sorted(a.name for a in await store.list_all_agents())
    assert names == ['agent-a']


async def test_append_audit_does_not_raise(store):
    event = AuditEvent(actor='agent-a', action='invoke', resource='invoice.verify', outcome='OK')
    await store.append_audit(event)


async def test_list_audit_events_returns_all_in_order(store):
    e1 = AuditEvent(actor='agent-a', action='register', resource='agent-a', outcome='OK')
    e2 = AuditEvent(actor='agent-b', action='register', resource='agent-b', outcome='OK')
    await store.append_audit(e1)
    await store.append_audit(e2)
    events = await store.list_audit_events()
    assert [e.event_id for e in events] == [e1.event_id, e2.event_id]


async def test_list_audit_events_filters_by_trace_id(store):
    matching = AuditEvent(actor='agent-a', action='invoke', resource='math.add', outcome='OK', trace_id='trace-1')
    other = AuditEvent(actor='agent-a', action='invoke', resource='math.add', outcome='OK', trace_id='trace-2')
    await store.append_audit(matching)
    await store.append_audit(other)
    events = await store.list_audit_events(trace_id='trace-1')
    assert [e.event_id for e in events] == [matching.event_id]


def make_approval(task_id: str = 'task-1', **overrides) -> Approval:
    defaults = {'task_id': task_id, 'expires_at': datetime.now(timezone.utc) + timedelta(minutes=30)}
    defaults.update(overrides)
    return Approval(**defaults)


async def test_save_and_get_approval_roundtrip(store):
    approval = make_approval(approvers=['alice'])
    await store.save_approval(approval)
    fetched = await store.get_approval(approval.approval_id)
    assert fetched is not None
    assert fetched.approvers == ['alice']
    assert fetched.state == 'PENDING'


async def test_get_approval_returns_none_for_unknown(store):
    assert await store.get_approval('does-not-exist') is None


async def test_save_approval_updates_state(store):
    approval = make_approval()
    await store.save_approval(approval)
    approval.state = 'APPROVED'
    await store.save_approval(approval)
    fetched = await store.get_approval(approval.approval_id)
    assert fetched.state == 'APPROVED'


async def test_list_pending_approvals_excludes_decided_ones(store):
    pending = make_approval(task_id='task-pending')
    approved = make_approval(task_id='task-approved', state='APPROVED')
    await store.save_approval(pending)
    await store.save_approval(approved)
    results = await store.list_pending_approvals()
    assert [a.approval_id for a in results] == [pending.approval_id]
