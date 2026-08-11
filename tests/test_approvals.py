import shutil

import pytest

from conet.control.approvals import (
    ApprovalAlreadyDecidedError,
    ApprovalWorkflow,
    NotAnAuthorizedApproverError,
    UnknownTaskError,
)
from conet.control.teams import _DEFAULT_POLICY_PATH as _TEAMS_DEFAULT_POLICY
from conet.control.teams import TeamService
from conet.persistence.store import Store
from conet.sdk.manifests import Task


@pytest.fixture
async def store():
    s = Store(':memory:')
    yield s
    await s.close()


@pytest.fixture
def teams(tmp_path):
    policy_path = str(tmp_path / 'human_roles_policy.csv')
    shutil.copy(_TEAMS_DEFAULT_POLICY, policy_path)
    return TeamService(role_policy_path=policy_path)


@pytest.fixture
def workflow(store, teams):
    return ApprovalWorkflow(store, teams)


@pytest.fixture
def workflow_no_teams(store):
    return ApprovalWorkflow(store)


async def make_task(store: Store, task_id: str = 'task-1', trace_id: str | None = None) -> Task:
    task = Task(task_id=task_id, requester='finance-agent', skill_id='invoice.pay', trace_id=trace_id)
    await store.save_task(task)
    return task


async def test_request_approval_sets_task_waiting_and_creates_pending_approval(store, workflow):
    await make_task(store)
    approval = await workflow.request_approval('task-1', approvers=['alice'])
    assert approval.state == 'PENDING'

    task = await store.get_task('task-1')
    assert task.state == 'WAITING_APPROVAL'


async def test_request_approval_raises_for_unknown_task(workflow):
    with pytest.raises(UnknownTaskError):
        await workflow.request_approval('does-not-exist', approvers=['alice'])


async def test_approve_by_assigned_approver_with_role_succeeds(store, workflow, teams):
    await make_task(store)
    await teams.assign_role('alice', 'Approver')
    approval = await workflow.request_approval('task-1', approvers=['alice'])

    decided = await workflow.approve(approval.approval_id, decided_by='alice')
    assert decided.state == 'APPROVED'

    task = await store.get_task('task-1')
    assert task.state == 'ROUTING'


async def test_approve_rejected_when_not_an_assigned_approver(store, workflow, teams):
    await make_task(store)
    await teams.assign_role('mallory', 'Approver')  # has the role, but wasn't assigned to this approval
    approval = await workflow.request_approval('task-1', approvers=['alice'])

    with pytest.raises(NotAnAuthorizedApproverError):
        await workflow.approve(approval.approval_id, decided_by='mallory')


async def test_approve_rejected_when_assigned_but_role_was_revoked(store, workflow, teams):
    await make_task(store)
    approval = await workflow.request_approval('task-1', approvers=['alice'])
    # alice is in the approvers list but was never granted the approve_task permission
    with pytest.raises(NotAnAuthorizedApproverError):
        await workflow.approve(approval.approval_id, decided_by='alice')


async def test_approve_without_team_service_only_checks_assignment(store, workflow_no_teams):
    await make_task(store)
    approval = await workflow_no_teams.request_approval('task-1', approvers=['alice'])
    decided = await workflow_no_teams.approve(approval.approval_id, decided_by='alice')
    assert decided.state == 'APPROVED'


async def test_reject_sets_task_and_approval_rejected(store, workflow, teams):
    await make_task(store)
    await teams.assign_role('alice', 'Approver')
    approval = await workflow.request_approval('task-1', approvers=['alice'])

    decided = await workflow.reject(approval.approval_id, decided_by='alice', metadata={'reason': 'too risky'})
    assert decided.state == 'REJECTED'
    assert decided.decision_metadata['reason'] == 'too risky'

    task = await store.get_task('task-1')
    assert task.state == 'REJECTED'


async def test_cannot_decide_an_already_decided_approval(store, workflow, teams):
    await make_task(store)
    await teams.assign_role('alice', 'Approver')
    approval = await workflow.request_approval('task-1', approvers=['alice'])
    await workflow.approve(approval.approval_id, decided_by='alice')

    with pytest.raises(ApprovalAlreadyDecidedError):
        await workflow.approve(approval.approval_id, decided_by='alice')


async def test_expire_overdue_expires_only_past_due_pending_approvals(store, workflow, teams):
    await make_task(store, 'task-expired')
    await make_task(store, 'task-fresh')
    await teams.assign_role('alice', 'Approver')

    expired_approval = await workflow.request_approval('task-expired', approvers=['alice'], ttl_seconds=-10)
    fresh_approval = await workflow.request_approval('task-fresh', approvers=['alice'], ttl_seconds=3600)

    expired = await workflow.expire_overdue()
    assert [a.approval_id for a in expired] == [expired_approval.approval_id]

    expired_task = await store.get_task('task-expired')
    assert expired_task.state == 'REJECTED'
    fresh_task = await store.get_task('task-fresh')
    assert fresh_task.state == 'WAITING_APPROVAL'

    still_fresh = await store.get_approval(fresh_approval.approval_id)
    assert still_fresh.state == 'PENDING'


async def test_audit_trail_carries_trace_id_through_approval_decision(store, workflow, teams):
    await make_task(store, 'task-1', trace_id='trace-xyz')
    await teams.assign_role('alice', 'Approver')
    approval = await workflow.request_approval('task-1', approvers=['alice'])
    await workflow.approve(approval.approval_id, decided_by='alice')

    events = await store.list_audit_events(trace_id='trace-xyz')
    actions = {e.action for e in events}
    assert {'request_approval', 'approve'} <= actions
