import json

import grpc
import httpx
import pytest
from google.protobuf.struct_pb2 import Struct

from conet.control.policy import PolicyEngine
from conet.gateway.webhook import WebhookAdapter, WebhookSkill
from conet.persistence.store import Store
from conet.protocols.grpc import skillruntime_pb2 as pb2
from conet.protocols.grpc import skillruntime_pb2_grpc as pb2_grpc
from conet.runtime.server import serve
from conet.sdk.manifests import SkillDef

_SKILL = SkillDef(
    skill_id='vendor.get_balance', version='1.0.0', side_effects='read_only',
    input_schema={'type': 'object', 'properties': {'customer_id': {'type': 'string'}}, 'required': ['customer_id']},
    output_schema={'type': 'object', 'properties': {'balance': {'type': 'number'}}},
)


def make_adapter(handler, *, headers=None) -> WebhookAdapter:
    webhook_skill = WebhookSkill(
        skill=_SKILL, url='https://vendor.example.com/customers/{payload[customer_id]}',
        headers=headers or {'Authorization': 'Bearer vendor-secret'},
    )
    return WebhookAdapter(
        [webhook_skill], endpoint='grpc://localhost:1', name='vendor-agent', department='finance',
        transport=httpx.MockTransport(handler),
    )


def test_describe_lists_the_configured_skill():
    adapter = make_adapter(lambda request: httpx.Response(200, json={}))
    manifest = adapter.describe()
    assert manifest.name == 'vendor-agent'
    assert manifest.framework == 'webhook'
    assert [s.skill_id for s in manifest.skills] == ['vendor.get_balance']


async def test_invoke_sends_the_url_templated_from_payload_and_static_headers():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen['method'] = request.method
        seen['path'] = request.url.path
        seen['headers'] = dict(request.headers)
        seen['body'] = json.loads(request.content)
        return httpx.Response(200, json={'balance': 42.5})

    adapter = make_adapter(handler)
    result = await adapter.invoke('vendor.get_balance', {'customer_id': 'C-4471'})

    assert result == {'balance': 42.5}
    assert seen['method'] == 'POST'
    assert seen['path'] == '/customers/C-4471'
    assert seen['headers']['authorization'] == 'Bearer vendor-secret'
    assert seen['body'] == {'customer_id': 'C-4471'}


async def test_headers_are_static_regardless_of_payload_content():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers['authorization'] == 'Bearer vendor-secret'
        return httpx.Response(200, json={'balance': 0})

    adapter = make_adapter(handler)
    await adapter.invoke('vendor.get_balance', {'customer_id': 'anything-{payload[nothing]}'})


async def test_non_2xx_response_raises():
    adapter = make_adapter(lambda request: httpx.Response(404, json={'error': 'not found'}))
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.invoke('vendor.get_balance', {'customer_id': 'C-4471'})


async def test_webhook_skill_is_reachable_through_the_normal_execute_path(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'balance': 42.5})

    adapter = make_adapter(handler)
    policy = PolicyEngine(secret_key='test-secret')
    policy.add_policy_rule('finance', 'vendor.get_balance', 'invoke')
    store = Store(str(tmp_path / 'conet.db'))

    grpc_server = await serve(adapter.describe(), adapter, policy, port=50175, store=store)
    channel = grpc.aio.insecure_channel('localhost:50175')
    stub = pb2_grpc.SkillRuntimeStub(channel)
    try:
        token = policy.mint_auth_context('finance', 'vendor.get_balance')
        payload = Struct()
        payload.update({'customer_id': 'C-4471'})
        resp = await stub.Execute(pb2.SkillRequest(
            skill_id='vendor.get_balance', task_id='t-webhook', auth_context=token, input=payload,
        ))
    finally:
        await channel.close()
        await grpc_server.stop(None)
        await store.close()

    assert resp.status == pb2.OK
    assert dict(resp.output) == {'balance': 42.5}
