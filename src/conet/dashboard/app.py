import os
import secrets

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.templating import Jinja2Templates

from conet.control.auth import User
from conet.control.teams import VALID_ROLES
from conet.dashboard.services import DashboardServices

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), 'templates')


def _network_payload(agents, tasks, pending_approvals, recent_audit) -> dict:
    """Nodes + edges for the network graph, and the monitoring stats strip
    above it. Edge direction/volume/last state come from recent tasks
    (list_recent_tasks is newest-first, so the first task seen per
    requester/provider pair is its most recent one)."""
    nodes = [
        {'name': a.name, 'department': a.department, 'framework': a.framework,
         'skills': [s.skill_id for s in a.skills]}
        for a in agents
    ]
    edge_counts: dict[tuple[str, str], dict] = {}
    for task in tasks:
        if task.provider is None:
            continue
        key = (task.requester, task.provider)
        entry = edge_counts.setdefault(key, {'count': 0, 'last_state': task.state})
        entry['count'] += 1
    edges = [
        {'source': src, 'target': dst, 'count': v['count'], 'last_state': v['last_state']}
        for (src, dst), v in edge_counts.items()
    ]
    stats = {
        'agents': len(agents),
        'recent_tasks': len(tasks),
        'denied_recent': sum(1 for e in recent_audit if e.outcome == 'DENIED'),
        'pending_approvals': len(pending_approvals),
    }
    return {'nodes': nodes, 'edges': edges, 'stats': stats}


def create_dashboard_app(services: DashboardServices) -> FastAPI:
    """One console where an operator sees the network, watches traffic,
    changes policy, approves tasks, and manages people (Feature Plan §B).
    Server-rendered HTML + HTMX; every write action also works as a plain
    form POST, so nothing here depends on JavaScript being enabled.
    """
    app = FastAPI(title='CoNET Operator Console')
    templates = Jinja2Templates(directory=_TEMPLATES_DIR)

    auth = services.auth
    current_user = auth.current_active_user
    cookie_transport = auth.cookie_backend.transport
    cookie_strategy = auth.cookie_backend.get_strategy()

    app.include_router(auth.fastapi_users.get_auth_router(auth.bearer_backend), prefix='/auth/jwt', tags=['auth'])
    app.include_router(auth.fastapi_users.get_register_router(auth.UserRead, auth.UserCreate), prefix='/auth', tags=['auth'])

    def render(request: Request, template: str, active: str, **context):
        return templates.TemplateResponse(request, template, {'active': active, **context})

    def set_login_cookie(response, token: str) -> None:
        response.set_cookie(
            cookie_transport.cookie_name, token, max_age=cookie_transport.cookie_max_age,
            path=cookie_transport.cookie_path, domain=cookie_transport.cookie_domain,
            secure=cookie_transport.cookie_secure, httponly=cookie_transport.cookie_httponly,
            samesite=cookie_transport.cookie_samesite,
        )

    async def require(user: User, action: str) -> None:
        if not await services.teams.can(str(user.id), action):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f'requires permission: {action}')

    # --- landing ---

    @app.get('/')
    async def index():
        return RedirectResponse('/dashboard/network')

    # --- auth pages (browser-facing; separate from the JSON /auth/* API routers above) ---

    @app.get('/dashboard/register')
    async def register_page(request: Request):
        return templates.TemplateResponse(request, 'register.html', {})

    @app.post('/dashboard/register')
    async def register_submit(
        request: Request, email: str = Form(...), password: str = Form(...),
        user_manager=Depends(auth.get_user_manager),
    ):
        try:
            user = await user_manager.create(auth.UserCreate(email=email, password=password))
        except Exception as exc:  # noqa: BLE001 -- fastapi-users raises assorted exceptions (duplicate email, weak password, ...); all become a user-facing form error, not a 500
            return templates.TemplateResponse(request, 'register.html', {'error': str(exc)}, status_code=400)
        if user.is_superuser:
            # is_superuser (auth.py) and a TeamService role are separate
            # systems by design (§A: agent RBAC vs. human roles) -- bridge
            # them here so the first user can actually use the role-gated
            # panels, not just hold an auth-level flag nothing here reads.
            await services.teams.assign_role(str(user.id), 'Owner')
        token = await cookie_strategy.write_token(user)
        response = RedirectResponse('/dashboard/network', status_code=status.HTTP_303_SEE_OTHER)
        set_login_cookie(response, token)
        return response

    @app.get('/dashboard/login')
    async def login_page(request: Request):
        return templates.TemplateResponse(request, 'login.html', {})

    @app.post('/dashboard/login')
    async def login_submit(
        request: Request, email: str = Form(...), password: str = Form(...),
        user_manager=Depends(auth.get_user_manager),
    ):
        user = await user_manager.authenticate(OAuth2PasswordRequestForm(username=email, password=password))
        if user is None or not user.is_active:
            return templates.TemplateResponse(request, 'login.html', {'error': 'invalid email or password'}, status_code=400)
        token = await cookie_strategy.write_token(user)
        response = RedirectResponse('/dashboard/network', status_code=status.HTTP_303_SEE_OTHER)
        set_login_cookie(response, token)
        return response

    @app.post('/dashboard/logout')
    async def logout_submit():
        response = RedirectResponse('/dashboard/login', status_code=status.HTTP_303_SEE_OTHER)
        response.delete_cookie(cookie_transport.cookie_name, path=cookie_transport.cookie_path)
        return response

    # --- network map (F2/F3/F8) ---

    @app.get('/dashboard/network')
    async def network(request: Request, user: User = Depends(current_user)):
        agents = await services.store.list_all_agents()
        tasks = await services.store.list_recent_tasks(limit=200)
        pending = await services.store.list_pending_approvals()
        recent_audit = await services.store.list_audit_events(limit=200)
        graph = _network_payload(agents, tasks, pending, recent_audit)
        return render(request, 'network.html', 'network', agents=agents, current_user=user, graph=graph)

    @app.get('/dashboard/api/network')
    async def network_api(user: User = Depends(current_user)):
        agents = await services.store.list_all_agents()
        tasks = await services.store.list_recent_tasks(limit=200)
        pending = await services.store.list_pending_approvals()
        recent_audit = await services.store.list_audit_events(limit=200)
        return _network_payload(agents, tasks, pending, recent_audit)

    # --- live traffic (F11, derived from Task/audit records) ---

    @app.get('/dashboard/traffic')
    async def traffic(request: Request, user: User = Depends(current_user)):
        tasks = await services.store.list_recent_tasks()
        return render(request, 'traffic.html', 'traffic', tasks=tasks, current_user=user)

    @app.get('/dashboard/api/traffic')
    async def traffic_api(user: User = Depends(current_user)):
        return await services.store.list_recent_tasks()

    # --- policy editor (F6) ---

    @app.get('/dashboard/policy')
    async def policy_page(request: Request, user: User = Depends(current_user)):
        can_manage = await services.teams.can(str(user.id), 'manage_policy')
        return render(
            request, 'policy.html', 'policy', current_user=user, can_manage=can_manage,
            rules=services.policy.list_policy_rules(),
        )

    @app.post('/dashboard/policy/add')
    async def policy_add(
        subject: str = Form(...), skill_id: str = Form(...), action: str = Form(...),
        user: User = Depends(current_user),
    ):
        await require(user, 'manage_policy')
        services.policy.add_policy_rule(subject, skill_id, action)
        return RedirectResponse('/dashboard/policy', status_code=status.HTTP_303_SEE_OTHER)

    @app.post('/dashboard/policy/remove')
    async def policy_remove(
        subject: str = Form(...), skill_id: str = Form(...), action: str = Form(...),
        user: User = Depends(current_user),
    ):
        await require(user, 'manage_policy')
        services.policy.remove_policy_rule(subject, skill_id, action)
        return RedirectResponse('/dashboard/policy', status_code=status.HTTP_303_SEE_OTHER)

    @app.get('/dashboard/policy/explain')
    async def policy_explain(
        request: Request, subject: str, skill_id: str, action: str = 'invoke', user: User = Depends(current_user),
    ):
        can_manage = await services.teams.can(str(user.id), 'manage_policy')
        explanation = await services.policy.explain_decision(subject, skill_id, action)
        return render(
            request, 'policy.html', 'policy', current_user=user, can_manage=can_manage,
            rules=services.policy.list_policy_rules(), explanation=explanation,
            explain_subject=subject, explain_skill_id=skill_id, explain_action=action,
        )

    # --- approvals queue (F7, §A2) ---

    @app.get('/dashboard/approvals')
    async def approvals_page(request: Request, user: User = Depends(current_user)):
        can_decide = await services.teams.can(str(user.id), 'approve_task')
        approvals = await services.store.list_pending_approvals()
        return render(request, 'approvals.html', 'approvals', current_user=user, can_decide=can_decide, approvals=approvals)

    @app.post('/dashboard/approvals/{approval_id}/approve')
    async def approve(approval_id: str, user: User = Depends(current_user)):
        await require(user, 'approve_task')
        await services.approvals.approve(approval_id, decided_by=str(user.id))
        return RedirectResponse('/dashboard/approvals', status_code=status.HTTP_303_SEE_OTHER)

    @app.post('/dashboard/approvals/{approval_id}/reject')
    async def reject(approval_id: str, user: User = Depends(current_user)):
        await require(user, 'approve_task')
        await services.approvals.reject(approval_id, decided_by=str(user.id))
        return RedirectResponse('/dashboard/approvals', status_code=status.HTTP_303_SEE_OTHER)

    # --- audit log (F11) ---

    @app.get('/dashboard/audit')
    async def audit_page(request: Request, trace_id: str | None = None, user: User = Depends(current_user)):
        await require(user, 'view_audit')
        events = await services.store.list_audit_events(trace_id=trace_id)
        return render(request, 'audit.html', 'audit', current_user=user, events=events, trace_id=trace_id)

    @app.get('/dashboard/api/audit')
    async def audit_api(trace_id: str | None = None, user: User = Depends(current_user)):
        await require(user, 'view_audit')
        return await services.store.list_audit_events(trace_id=trace_id, limit=100)

    # --- integrations / MCP gateway (F12) ---

    @app.get('/dashboard/integrations')
    async def integrations_page(request: Request, user: User = Depends(current_user)):
        can_manage = await services.teams.can(str(user.id), 'manage_integrations')
        return render(
            request, 'integrations.html', 'integrations', current_user=user, can_manage=can_manage,
            servers=services.mcp_gateway.list_connected_servers(),
        )

    @app.post('/dashboard/integrations/connect')
    async def integrations_connect(
        server_name: str = Form(...), command: str = Form(...), args: str = Form(''), env: str = Form(''),
        user: User = Depends(current_user),
    ):
        await require(user, 'manage_integrations')
        parsed_args = args.split() if args.strip() else []
        parsed_env = {}
        for pair in env.split(','):
            pair = pair.strip()
            if not pair:
                continue
            key, _, value = pair.partition('=')
            parsed_env[key.strip()] = value.strip()

        await services.mcp_gateway.connect_server(server_name, command=command, args=parsed_args, env=parsed_env or None)
        await services.mcp_gateway.import_capabilities(server_name)
        return RedirectResponse('/dashboard/integrations', status_code=status.HTTP_303_SEE_OTHER)

    @app.post('/dashboard/integrations/disconnect')
    async def integrations_disconnect(server_name: str = Form(...), user: User = Depends(current_user)):
        await require(user, 'manage_integrations')
        await services.mcp_gateway.disconnect_server(server_name)
        return RedirectResponse('/dashboard/integrations', status_code=status.HTTP_303_SEE_OTHER)

    # --- team & accounts (§A1/§A2) ---

    @app.get('/dashboard/team')
    async def team_page(request: Request, user: User = Depends(current_user)):
        can_manage = await services.teams.can(str(user.id), 'manage_team')
        users = await auth.list_users()
        roles = {str(u.id): await services.teams.get_role(str(u.id)) for u in users}
        return render(
            request, 'team.html', 'team', current_user=user, can_manage=can_manage,
            users=users, roles=roles, valid_roles=VALID_ROLES,
        )

    @app.post('/dashboard/team/assign')
    async def team_assign(user_id: str = Form(...), role: str = Form(...), user: User = Depends(current_user)):
        await require(user, 'manage_team')
        await services.teams.assign_role(user_id, role)
        return RedirectResponse('/dashboard/team', status_code=status.HTTP_303_SEE_OTHER)

    @app.post('/dashboard/team/invite')
    async def team_invite(
        request: Request, email: str = Form(...), role: str = Form(...),
        user: User = Depends(current_user), user_manager=Depends(auth.get_user_manager),
    ):
        await require(user, 'manage_team')
        temp_password = secrets.token_urlsafe(12)
        invitee = await user_manager.create(auth.UserCreate(email=email, password=temp_password))
        await services.teams.invite(str(invitee.id), role)

        can_manage = True
        users = await auth.list_users()
        roles = {str(u.id): await services.teams.get_role(str(u.id)) for u in users}
        return render(
            request, 'team.html', 'team', current_user=user, can_manage=can_manage,
            users=users, roles=roles, valid_roles=VALID_ROLES,
            invited_email=email, invited_password=temp_password,
        )

    return app
