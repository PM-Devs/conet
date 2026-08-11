import httpx
import pytest
from fastapi import Depends, FastAPI

from conet.control.auth import User, create_auth_module


@pytest.fixture
async def auth_client(tmp_path):
    db_path = str(tmp_path / 'users.db')
    auth = create_auth_module(db_path=db_path, secret='test-secret')
    await auth.create_db_and_tables()

    app = FastAPI()
    app.include_router(auth.fastapi_users.get_auth_router(auth.bearer_backend), prefix='/auth/jwt', tags=['auth'])
    app.include_router(auth.fastapi_users.get_register_router(auth.UserRead, auth.UserCreate), prefix='/auth', tags=['auth'])

    @app.get('/whoami')
    async def whoami(user: User = Depends(auth.current_active_user)):
        return {'email': user.email, 'is_superuser': user.is_superuser}

    @app.get('/admin-only')
    async def admin_only(user: User = Depends(auth.current_superuser)):
        return {'ok': True}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
        yield client

    await auth.engine.dispose()


async def _register(client: httpx.AsyncClient, email: str, password: str = 'a-strong-password') -> httpx.Response:
    return await client.post('/auth/register', json={'email': email, 'password': password})


async def _login(client: httpx.AsyncClient, email: str, password: str = 'a-strong-password') -> str:
    resp = await client.post('/auth/jwt/login', data={'username': email, 'password': password})
    assert resp.status_code == 200, resp.text
    return resp.json()['access_token']


async def test_first_user_becomes_superuser(auth_client):
    resp = await _register(auth_client, 'owner@example.com')
    assert resp.status_code == 201, resp.text
    assert resp.json()['is_superuser'] is True


async def test_second_user_is_not_superuser(auth_client):
    await _register(auth_client, 'owner@example.com')
    resp = await _register(auth_client, 'worker@example.com')
    assert resp.status_code == 201, resp.text
    assert resp.json()['is_superuser'] is False


async def test_login_then_access_protected_endpoint(auth_client):
    await _register(auth_client, 'owner@example.com')
    token = await _login(auth_client, 'owner@example.com')

    resp = await auth_client.get('/whoami', headers={'Authorization': f'Bearer {token}'})
    assert resp.status_code == 200
    assert resp.json() == {'email': 'owner@example.com', 'is_superuser': True}


async def test_protected_endpoint_rejects_missing_token(auth_client):
    resp = await auth_client.get('/whoami')
    assert resp.status_code == 401


async def test_superuser_only_endpoint_rejects_regular_user(auth_client):
    await _register(auth_client, 'owner@example.com')
    await _register(auth_client, 'worker@example.com')
    token = await _login(auth_client, 'worker@example.com')

    resp = await auth_client.get('/admin-only', headers={'Authorization': f'Bearer {token}'})
    assert resp.status_code == 403


async def test_superuser_only_endpoint_allows_first_user(auth_client):
    await _register(auth_client, 'owner@example.com')
    token = await _login(auth_client, 'owner@example.com')

    resp = await auth_client.get('/admin-only', headers={'Authorization': f'Bearer {token}'})
    assert resp.status_code == 200


async def test_login_rejects_wrong_password(auth_client):
    await _register(auth_client, 'owner@example.com')
    resp = await auth_client.post('/auth/jwt/login', data={'username': 'owner@example.com', 'password': 'wrong'})
    assert resp.status_code == 400
