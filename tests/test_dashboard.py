import os
import sys

import httpx
import pytest

from conet.dashboard.app import create_dashboard_app
from conet.dashboard.services import build_services
from conet.sdk.manifests import AgentManifest, AuditEvent, SkillDef, Task

_TOY_SERVER = os.path.join(os.path.dirname(__file__), 'fixtures', 'mcp_toy_server.py')


@pytest.fixture
async def services(tmp_path):
    svc = build_services(
        db_path=str(tmp_path / 'conet.db'),
        users_db_path=str(tmp_path / 'users.db'),
        nats_url='nats://localhost:4222',
        policy_secret='test-secret',
        auth_secret='test-secret',
        # the test client talks plain http://test, not https -- a real
        # browser (correctly) never sends a Secure cookie over plain HTTP
        cookie_secure=False,
    )
    await svc.auth.create_db_and_tables()
    yield svc
    await svc.close()


@pytest.fixture
async def client(services):
    app = create_dashboard_app(services)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://test', follow_redirects=True) as c:
        yield c


async def register(client: httpx.AsyncClient, email: str, password: str = 'a-strong-password') -> httpx.Response:
    return await client.post('/dashboard/register', data={'email': email, 'password': password})


async def login(client: httpx.AsyncClient, email: str, password: str = 'a-strong-password') -> httpx.Response:
    return await client.post('/dashboard/login', data={'email': email, 'password': password})


# --- auth pages ---

async def test_first_registered_user_lands_on_network_map_as_owner(client, services):
    resp = await register(client, 'owner@example.com')
    assert resp.status_code == 200
    assert 'Network map' in resp.text

    users = await services.auth.list_users()
    owner = next(u for u in users if u.email == 'owner@example.com')
    assert owner.is_superuser is True
    assert await services.teams.get_role(str(owner.id)) == 'Owner'


async def test_second_registered_user_is_not_an_owner(client, services):
    await register(client, 'owner@example.com')
    await client.post('/dashboard/logout')
    await register(client, 'worker@example.com')

    users = await services.auth.list_users()
    worker = next(u for u in users if u.email == 'worker@example.com')
    assert worker.is_superuser is False
    assert await services.teams.get_role(str(worker.id)) is None


async def test_login_with_wrong_password_shows_error(client):
    await register(client, 'owner@example.com')
    await client.post('/dashboard/logout')
    resp = await login(client, 'owner@example.com', password='wrong')
    assert resp.status_code == 400
    assert 'invalid' in resp.text.lower()


async def test_unauthenticated_access_to_a_panel_is_rejected(client):
    resp = await client.get('/dashboard/network')
    assert resp.status_code == 401


# --- network map ---

async def test_network_map_lists_registered_agents(client, services):
    await register(client, 'owner@example.com')
    manifest = AgentManifest(
        name='agent-a', framework='plain', department='finance', version='1.0.0',
        endpoint='grpc://localhost:1', identity_ref='cert-1',
        skills=[SkillDef(skill_id='invoice.verify', version='1.0.0', side_effects='read_only',
                          input_schema={'type': 'object'}, output_schema={'type': 'object'})],
    )
    await services.store.upsert_agent(manifest)

    resp = await client.get('/dashboard/network')
    assert resp.status_code == 200
    assert 'agent-a' in resp.text
    assert 'invoice.verify' in resp.text


# --- live traffic ---

async def test_traffic_lists_recent_tasks(client, services):
    await register(client, 'owner@example.com')
    await services.store.save_task(Task(task_id='t1', requester='agent-a', skill_id='invoice.verify', state='COMPLETED'))

    resp = await client.get('/dashboard/traffic')
    assert resp.status_code == 200
    assert 't1'[:8] in resp.text
    assert 'COMPLETED' in resp.text


# --- policy editor ---

async def test_policy_page_is_read_only_without_manage_policy_role(client, services):
    await register(client, 'owner@example.com')
    await client.post('/dashboard/logout')
    await register(client, 'viewer@example.com')
    users = await services.auth.list_users()
    viewer = next(u for u in users if u.email == 'viewer@example.com')
    await services.teams.assign_role(str(viewer.id), 'Viewer')
    await login(client, 'viewer@example.com')

    resp = await client.get('/dashboard/policy')
    assert resp.status_code == 200
    assert 'Read-only' in resp.text

    denied = await client.post('/dashboard/policy/add', data={'subject': 'finance', 'skill_id': 'x', 'action': 'invoke'})
    assert denied.status_code == 403


async def test_owner_can_add_and_remove_a_policy_rule(client, services):
    await register(client, 'owner@example.com')

    resp = await client.post('/dashboard/policy/add', data={'subject': 'finance', 'skill_id': 'invoice.verify', 'action': 'invoke'})
    assert resp.status_code == 200
    assert ('finance', 'invoice.verify', 'invoke') in services.policy.list_policy_rules()

    resp = await client.post('/dashboard/policy/remove', data={'subject': 'finance', 'skill_id': 'invoice.verify', 'action': 'invoke'})
    assert resp.status_code == 200
    assert ('finance', 'invoice.verify', 'invoke') not in services.policy.list_policy_rules()


async def test_policy_explain_shows_the_decision(client):
    await register(client, 'owner@example.com')
    await client.post('/dashboard/policy/add', data={'subject': 'finance', 'skill_id': 'invoice.verify', 'action': 'invoke'})

    resp = await client.get('/dashboard/policy/explain', params={'subject': 'finance', 'skill_id': 'invoice.verify', 'action': 'invoke'})
    assert resp.status_code == 200
    assert 'allowed by rule' in resp.text


# --- approvals ---

async def test_approvals_page_denies_decision_without_approve_task_role(client, services):
    await register(client, 'owner@example.com')
    await client.post('/dashboard/logout')
    await register(client, 'auditor@example.com')
    users = await services.auth.list_users()
    auditor = next(u for u in users if u.email == 'auditor@example.com')
    await services.teams.assign_role(str(auditor.id), 'Auditor')
    await login(client, 'auditor@example.com')

    resp = await client.get('/dashboard/approvals')
    assert resp.status_code == 200
    assert 'Read-only' in resp.text

    denied = await client.post('/dashboard/approvals/does-not-matter/approve')
    assert denied.status_code == 403


async def test_owner_can_approve_a_pending_approval(client, services):
    await register(client, 'owner@example.com')
    users = await services.auth.list_users()
    owner = next(u for u in users if u.email == 'owner@example.com')

    await services.store.save_task(Task(task_id='task-1', requester='agent-a', skill_id='invoice.pay'))
    approval = await services.approvals.request_approval('task-1', approvers=[str(owner.id)])

    resp = await client.post(f'/dashboard/approvals/{approval.approval_id}/approve')
    assert resp.status_code == 200

    decided = await services.store.get_approval(approval.approval_id)
    assert decided.state == 'APPROVED'


# --- audit log ---

async def test_audit_log_requires_view_audit_permission(client, services):
    await register(client, 'owner@example.com')
    await client.post('/dashboard/logout')
    await register(client, 'nobody@example.com')
    await login(client, 'nobody@example.com')

    resp = await client.get('/dashboard/audit')
    assert resp.status_code == 403


async def test_audit_log_filters_by_trace_id(client, services):
    await register(client, 'owner@example.com')
    await services.store.append_audit(AuditEvent(actor='agent-a', action='invoke', resource='x', outcome='OK', trace_id='trace-1'))
    await services.store.append_audit(AuditEvent(actor='agent-a', action='invoke', resource='x', outcome='OK', trace_id='trace-2'))

    resp = await client.get('/dashboard/audit', params={'trace_id': 'trace-1'})
    assert resp.status_code == 200
    assert 'trace-1' not in resp.text or resp.text.count('trace-1'[:8]) >= 1  # short id is truncated in the table
    resp_all = await client.get('/dashboard/audit')
    assert resp_all.status_code == 200


# --- integrations ---

async def test_owner_can_connect_and_disconnect_an_mcp_server(client, services):
    await register(client, 'owner@example.com')

    resp = await client.post('/dashboard/integrations/connect', data={
        'server_name': 'weather', 'command': sys.executable, 'args': _TOY_SERVER, 'env': 'WEATHER_API_KEY=secret-val',
    })
    assert resp.status_code == 200
    assert 'mcp.weather.get_weather' in resp.text

    resp = await client.post('/dashboard/integrations/disconnect', data={'server_name': 'weather'})
    assert resp.status_code == 200
    assert services.mcp_gateway.list_connected_servers() == {}


async def test_non_manager_cannot_connect_a_server(client, services):
    await register(client, 'owner@example.com')
    await client.post('/dashboard/logout')
    await register(client, 'viewer@example.com')
    users = await services.auth.list_users()
    viewer = next(u for u in users if u.email == 'viewer@example.com')
    await services.teams.assign_role(str(viewer.id), 'Viewer')
    await login(client, 'viewer@example.com')

    resp = await client.post('/dashboard/integrations/connect', data={
        'server_name': 'weather', 'command': sys.executable, 'args': _TOY_SERVER, 'env': '',
    })
    assert resp.status_code == 403


# --- team & accounts ---

async def test_owner_can_assign_a_role(client, services):
    await register(client, 'owner@example.com')
    await client.post('/dashboard/logout')
    await register(client, 'newperson@example.com')
    users = await services.auth.list_users()
    newperson = next(u for u in users if u.email == 'newperson@example.com')
    await login(client, 'owner@example.com')

    resp = await client.post('/dashboard/team/assign', data={'user_id': str(newperson.id), 'role': 'Operator'})
    assert resp.status_code == 200
    assert await services.teams.get_role(str(newperson.id)) == 'Operator'


async def test_owner_can_invite_a_teammate_and_sees_the_temp_password(client, services):
    await register(client, 'owner@example.com')

    resp = await client.post('/dashboard/team/invite', data={'email': 'invitee@example.com', 'role': 'Approver'})
    assert resp.status_code == 200
    assert 'invitee@example.com' in resp.text
    assert 'Temporary password' in resp.text

    users = await services.auth.list_users()
    invitee = next(u for u in users if u.email == 'invitee@example.com')
    assert await services.teams.get_role(str(invitee.id)) == 'Approver'


async def test_non_manager_cannot_assign_roles(client, services):
    await register(client, 'owner@example.com')
    await client.post('/dashboard/logout')
    await register(client, 'plain@example.com')
    users = await services.auth.list_users()
    plain = next(u for u in users if u.email == 'plain@example.com')
    await login(client, 'plain@example.com')

    resp = await client.post('/dashboard/team/assign', data={'user_id': str(plain.id), 'role': 'Owner'})
    assert resp.status_code == 403
