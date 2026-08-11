import pytest

from conet.control.teams import TeamService, UnknownRoleError


@pytest.fixture
def teams(tmp_path):
    # each test gets its own policy file copy so role assignments don't bleed
    import shutil

    from conet.control.teams import _DEFAULT_POLICY_PATH
    policy_path = str(tmp_path / 'human_roles_policy.csv')
    shutil.copy(_DEFAULT_POLICY_PATH, policy_path)
    return TeamService(role_policy_path=policy_path)


async def test_assign_role_then_get_role(teams):
    await teams.assign_role('user-1', 'Admin')
    assert await teams.get_role('user-1') == 'Admin'


async def test_get_role_returns_none_for_unassigned_user(teams):
    assert await teams.get_role('nobody') is None


async def test_assign_role_rejects_unknown_role(teams):
    with pytest.raises(UnknownRoleError):
        await teams.assign_role('user-1', 'SuperWizard')


async def test_reassigning_role_replaces_the_old_one(teams):
    await teams.assign_role('user-1', 'Viewer')
    await teams.assign_role('user-1', 'Admin')
    assert await teams.get_role('user-1') == 'Admin'


async def test_revoke_removes_the_role(teams):
    await teams.assign_role('user-1', 'Admin')
    await teams.revoke('user-1')
    assert await teams.get_role('user-1') is None


async def test_admin_can_manage_policy(teams):
    await teams.assign_role('user-1', 'Admin')
    assert await teams.can('user-1', 'manage_policy') is True


async def test_viewer_cannot_manage_policy(teams):
    await teams.assign_role('user-1', 'Viewer')
    assert await teams.can('user-1', 'manage_policy') is False


async def test_viewer_can_view_audit(teams):
    await teams.assign_role('user-1', 'Viewer')
    assert await teams.can('user-1', 'view_audit') is True


async def test_approver_can_approve_but_not_manage_team(teams):
    await teams.assign_role('user-1', 'Approver')
    assert await teams.can('user-1', 'approve_task') is True
    assert await teams.can('user-1', 'manage_team') is False


async def test_unassigned_user_denied_by_default(teams):
    assert await teams.can('ghost', 'view_audit') is False


async def test_invite_is_equivalent_to_assign_role(teams):
    await teams.invite('user-2', 'Operator')
    assert await teams.get_role('user-2') == 'Operator'
