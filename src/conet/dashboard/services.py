import asyncio
import os
from dataclasses import dataclass
from types import SimpleNamespace

from conet.control.approvals import ApprovalWorkflow
from conet.control.auth import create_auth_module
from conet.control.discovery import Discovery
from conet.control.policy import PolicyEngine
from conet.control.registry import Registry
from conet.control.teams import TeamService
from conet.gateway.mcp.gateway import MCPGateway
from conet.persistence.store import Store
from conet.protocols.events.bus import EventBus
from conet.runtime.router import Router


@dataclass
class DashboardServices:
    """Everything a dashboard route needs, wired together once at startup."""

    store: Store
    event_bus: EventBus
    policy: PolicyEngine
    registry: Registry
    discovery: Discovery
    router: Router
    approvals: ApprovalWorkflow
    teams: TeamService
    mcp_gateway: MCPGateway
    auth: SimpleNamespace  # from create_auth_module — see conet.control.auth

    async def close(self) -> None:
        await self.mcp_gateway.close_all()
        await self.event_bus.close()
        await self.auth.engine.dispose()
        await self.store.close()


def build_services(
    db_path: str | None = None,
    users_db_path: str | None = None,
    use_single_db: bool | None = True,
    nats_url: str | None = None,
    policy_secret: str | None = None,
    policy_path: str | None = None,
    human_roles_policy_path: str | None = None,
    auth_secret: str | None = None,
    cookie_secure: bool | None = None,
) -> DashboardServices:
    # Resolve control DB path (agents/tasks/audit)
    resolved_db_path = db_path or os.environ.get('CONET_DB_PATH', 'conet.db')
    # By default use a single DB for both control plane and users to make
    # local/demo runs simpler. To opt out, pass use_single_db=False or set
    # CONET_SINGLE_DB=0 in the environment.
    resolved_use_single = use_single_db if use_single_db is not None else (os.environ.get('CONET_SINGLE_DB') == '1')
    store = Store(resolved_db_path)
    event_bus = EventBus(nats_url or os.environ.get('CONET_NATS_URL', 'nats://localhost:4222'))
    policy = PolicyEngine(
        secret_key=policy_secret or os.environ.get('CONET_POLICY_SECRET', 'dev-secret-change-me'),
        policy_path=policy_path or os.environ.get('CONET_POLICY_PATH'),
    )
    registry = Registry(store, event_bus)
    discovery = Discovery(store, policy)
    router = Router(discovery, policy)
    teams = TeamService(role_policy_path=human_roles_policy_path)
    approvals = ApprovalWorkflow(store, teams)
    mcp_gateway = MCPGateway(policy, store)
    # Resolve users DB path. If single-db mode is active, reuse the control DB
    # path for users; otherwise prefer explicit users_db_path or env var.
    resolved_users_db = (
        resolved_db_path if resolved_use_single else (users_db_path or os.environ.get('CONET_USERS_DB_PATH', 'conet_users.db'))
    )

    auth = create_auth_module(
        db_path=resolved_users_db,
        secret=auth_secret or os.environ.get('CONET_AUTH_SECRET'),
        cookie_secure=cookie_secure,
    )

    # Ensure the users DB schema is created before the dashboard serves requests.
    # create_db_and_tables is an async helper returned by create_auth_module.
    asyncio.run(auth.create_db_and_tables())

    return DashboardServices(
        store=store, event_bus=event_bus, policy=policy, registry=registry, discovery=discovery,
        router=router, approvals=approvals, teams=teams, mcp_gateway=mcp_gateway, auth=auth,
    )
