import asyncio

from fastapi.testclient import TestClient

from conet.dashboard.app import create_dashboard_app
from conet.dashboard.services import build_services


def test_dashboard_register_page(tmp_path):
    # Use temporary DB files to avoid touching the workspace DBs
    users_db = str(tmp_path / "users.db")
    state_db = str(tmp_path / "conet.db")

    services = build_services(
        db_path=state_db,
        users_db_path=users_db,
        nats_url="nats://127.0.0.1:4222",
        policy_secret="test-policy-secret",
        auth_secret="test-auth-secret",
        cookie_secure=True,
    )

    # Ensure the users table exists
    asyncio.run(services.auth.create_db_and_tables())

    app = create_dashboard_app(services)
    client = TestClient(app)

    # Register page should be accessible (HTTP 200)
    resp = client.get("/dashboard/register")
    assert resp.status_code == 200

    # No users initially
    users = asyncio.run(services.auth.list_users())
    assert isinstance(users, list)
