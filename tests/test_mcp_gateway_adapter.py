import os
import sys

import grpc
from conftest import requires_nats
from google.protobuf.struct_pb2 import Struct

from conet.control.policy import PolicyEngine
from conet.gateway.mcp.adapter import GatewayAdapter
from conet.gateway.mcp.gateway import MCPGateway
from conet.persistence.store import Store
from conet.protocols.grpc import skillruntime_pb2 as pb2
from conet.protocols.grpc import skillruntime_pb2_grpc as pb2_grpc
from conet.sdk.agent import start

pytestmark = requires_nats

_TOY_SERVER = os.path.join(os.path.dirname(__file__), 'fixtures', 'mcp_toy_server.py')
_FIXTURE_POLICY = os.path.join(os.path.dirname(__file__), 'fixtures', 'policy_mcp.csv')  # finance -> mcp.weather.get_weather
_SECRET = 'sk-live-super-secret-do-not-leak'


async def test_gateway_capabilities_are_reachable_through_the_normal_router_path(tmp_path):
    db_path = str(tmp_path / 'conet.db')
    inner_policy = PolicyEngine(secret_key='unused-by-gateway-adapter-path')
    gateway = MCPGateway(inner_policy)
    await gateway.connect_server('weather', command=sys.executable, args=[_TOY_SERVER], env={'WEATHER_API_KEY': _SECRET})
    try:
        skills = await gateway.import_capabilities('weather')
        adapter = GatewayAdapter(gateway, skills, endpoint='grpc://localhost:50230')

        running = await start(adapter, db_path=db_path, nats_url='nats://localhost:4222', policy_path=_FIXTURE_POLICY)
        try:
            outer_policy = PolicyEngine(secret_key='dev-secret-change-me')  # start()'s default secret

            # authorized requester: reaches the MCP tool through an ordinary Execute() call
            token = outer_policy.mint_auth_context('finance', 'mcp.weather.get_weather')
            channel = grpc.aio.insecure_channel('localhost:50230')
            stub = pb2_grpc.SkillRuntimeStub(channel)
            payload = Struct()
            payload.update({'city': 'Accra'})
            resp = await stub.Execute(pb2.SkillRequest(
                skill_id='mcp.weather.get_weather', task_id='t-authorized', auth_context=token, input=payload,
            ))
            await channel.close()

            assert resp.status == pb2.OK
            assert 'sunny' in dict(resp.output)['content'][0]['text']

            # unauthorized requester: denied by the outer SkillServer before the MCP tool is ever called
            denied_token = outer_policy.mint_auth_context('marketing', 'mcp.weather.get_weather')
            channel = grpc.aio.insecure_channel('localhost:50230')
            stub = pb2_grpc.SkillRuntimeStub(channel)
            payload = Struct()
            payload.update({'city': 'Accra'})
            resp = await stub.Execute(pb2.SkillRequest(
                skill_id='mcp.weather.get_weather', task_id='t-denied', auth_context=denied_token, input=payload,
            ))
            await channel.close()

            assert resp.status == pb2.DENIED
        finally:
            await running.stop()

        # the outer SkillServer's own audit trail attributes both calls to the true requester
        store = Store(db_path)
        events = await store.list_audit_events()
        await store.close()
        assert any(e.actor == 'finance' and e.outcome == 'OK' for e in events)
        assert any(e.actor == 'marketing' and e.outcome == 'DENIED' for e in events)
    finally:
        await gateway.close_all()


async def test_gateway_adapter_describe_lists_the_imported_skills():
    policy = PolicyEngine(secret_key='unused')
    gateway = MCPGateway(policy)
    await gateway.connect_server('weather', command=sys.executable, args=[_TOY_SERVER], env={'WEATHER_API_KEY': _SECRET})
    try:
        skills = await gateway.import_capabilities('weather')
        adapter = GatewayAdapter(gateway, skills, endpoint='grpc://localhost:50231')
        manifest = adapter.describe()
        assert manifest.name == 'mcp-gateway'
        assert [s.skill_id for s in manifest.skills] == ['mcp.weather.get_weather']
    finally:
        await gateway.close_all()
