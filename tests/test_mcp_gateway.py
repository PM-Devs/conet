import os
import sys
from contextlib import asynccontextmanager

import pytest

from conet.control.policy import PolicyEngine
from conet.gateway.mcp.gateway import MCPGateway, UnknownCapabilityError, UnknownServerError
from conet.persistence.store import Store

_TOY_SERVER = os.path.join(os.path.dirname(__file__), 'fixtures', 'mcp_toy_server.py')
_FIXTURE_POLICY = os.path.join(os.path.dirname(__file__), 'fixtures', 'policy_mcp.csv')  # finance -> mcp.weather.get_weather
_SECRET = 'sk-live-super-secret-do-not-leak'


@pytest.fixture
async def store():
    s = Store(':memory:')
    yield s
    await s.close()


@pytest.fixture
def policy():
    return PolicyEngine(secret_key='test-secret', policy_path=_FIXTURE_POLICY)


@asynccontextmanager
async def connected_gateway(policy: PolicyEngine, store: Store, server_name: str = 'weather'):
    """Connect and disconnect within one unbroken async context, all inside
    the calling test's own task — anyio's stdio_client/ClientSession tie an
    internal task group's cancel scope to whichever task opened it, and
    splitting open/close across a pytest fixture's yield boundary can hand
    the close half to a different task, raising a spurious teardown error."""
    gw = MCPGateway(policy, store)
    await gw.connect_server(server_name, command=sys.executable, args=[_TOY_SERVER], env={'WEATHER_API_KEY': _SECRET})
    try:
        yield gw
    finally:
        await gw.close_all()


async def test_import_capabilities_maps_the_tool_to_a_namespaced_skill(policy, store):
    async with connected_gateway(policy, store) as gateway:
        skills = await gateway.import_capabilities('weather')
        assert len(skills) == 1
        assert skills[0].skill_id == 'mcp.weather.get_weather'
        assert 'city' in skills[0].input_schema['properties']


async def test_credential_never_appears_in_the_imported_skill(policy, store):
    async with connected_gateway(policy, store) as gateway:
        skills = await gateway.import_capabilities('weather')
        skill_json = skills[0].model_dump_json()
        assert _SECRET not in skill_json
        assert 'WEATHER_API_KEY' not in skill_json


async def test_invoke_external_capability_denies_unauthorized_requester(policy, store):
    async with connected_gateway(policy, store) as gateway:
        await gateway.import_capabilities('weather')
        with pytest.raises(PermissionError):
            await gateway.invoke_external_capability('marketing', 'mcp.weather.get_weather', {'city': 'Accra'})


async def test_invoke_external_capability_denial_is_audited(policy, store):
    async with connected_gateway(policy, store) as gateway:
        await gateway.import_capabilities('weather')
        with pytest.raises(PermissionError):
            await gateway.invoke_external_capability('marketing', 'mcp.weather.get_weather', {'city': 'Accra'})

    events = await store.list_audit_events()
    assert any(e.outcome == 'DENIED' and e.actor == 'marketing' for e in events)


async def test_invoke_external_capability_succeeds_for_authorized_requester(policy, store):
    async with connected_gateway(policy, store) as gateway:
        await gateway.import_capabilities('weather')
        result = await gateway.invoke_external_capability('finance', 'mcp.weather.get_weather', {'city': 'Accra'})
        text = result['content'][0]['text']
        assert 'sunny' in text


async def test_invoke_external_capability_success_is_audited(policy, store):
    async with connected_gateway(policy, store) as gateway:
        await gateway.import_capabilities('weather')
        await gateway.invoke_external_capability('finance', 'mcp.weather.get_weather', {'city': 'Accra'})

    events = await store.list_audit_events()
    assert any(e.outcome == 'OK' and e.actor == 'finance' for e in events)


async def test_invoke_unknown_capability_raises(policy, store):
    async with connected_gateway(policy, store) as gateway:
        with pytest.raises(UnknownCapabilityError):
            await gateway.invoke_external_capability('finance', 'mcp.weather.not_a_real_tool', {})


async def test_import_capabilities_from_unconnected_server_raises(policy, store):
    gateway = MCPGateway(policy, store)
    with pytest.raises(UnknownServerError):
        await gateway.import_capabilities('nobody-connected-this')


async def test_list_connected_servers_keeps_each_servers_capabilities_independent(policy, store):
    gateway = MCPGateway(policy, store)
    try:
        # two real connections to the same toy server script, under different names
        await gateway.connect_server('weather-a', command=sys.executable, args=[_TOY_SERVER], env={'WEATHER_API_KEY': _SECRET})
        await gateway.connect_server('weather-b', command=sys.executable, args=[_TOY_SERVER], env={'WEATHER_API_KEY': _SECRET})
        await gateway.import_capabilities('weather-a')
        await gateway.import_capabilities('weather-b')

        result = gateway.list_connected_servers()
        assert result['weather-a'] == ['mcp.weather-a.get_weather']
        assert result['weather-b'] == ['mcp.weather-b.get_weather']
        # the historical bug shared one list across every key -- guard against regressing to it
        assert result['weather-a'] is not result['weather-b']
    finally:
        await gateway.close_all()


async def test_disconnect_server_drops_its_capabilities(policy, store):
    async with connected_gateway(policy, store) as gateway:
        await gateway.import_capabilities('weather')
        await gateway.disconnect_server('weather')

        with pytest.raises(UnknownCapabilityError):
            await gateway.invoke_external_capability('finance', 'mcp.weather.get_weather', {'city': 'Accra'})


async def test_disconnect_unknown_server_raises(policy, store):
    gateway = MCPGateway(policy, store)
    with pytest.raises(UnknownServerError):
        await gateway.disconnect_server('never-connected')
