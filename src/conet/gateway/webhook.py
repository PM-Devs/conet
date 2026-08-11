import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from conet.sdk.agent import Agent
from conet.sdk.manifests import AgentManifest, SkillDef

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)


def _import_httpx():
    # httpx is an optional dependency (onboarding a non-MCP vendor agent is
    # a narrower need than the core SDK) -- importing it lazily here, the
    # same way conet.gateway.mcp.gateway defers importing mcp, means the
    # rest of the package stays importable without pip install
    # 'colonynet[webhook]'.
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError(
            "httpx is not installed — it's an optional dependency: pip install 'colonynet[webhook]'",
        ) from exc
    return httpx


@dataclass
class WebhookSkill:
    """One vendor endpoint, declared as configuration — no code. `url` may
    reference the incoming payload with str.format placeholders, e.g.
    "https://vendor.example.com/customers/{payload[customer_id]}".

    `headers` is fixed at configuration time and never influenced by the
    caller's payload — the same credential-isolation principle the MCP
    Gateway uses, just for a REST vendor instead of a subprocess env."""

    skill: SkillDef
    url: str
    method: str = 'POST'
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 30.0


class WebhookAdapter:
    """Onboards a third-party or vendor agent that only exposes a plain
    REST/webhook API — no MCP, no CoNET code on their side — as a normal,
    governed CoNET agent (Feature Plan F12-adjacent; see README 'Governing
    third-party and vendor agents'). Each declared WebhookSkill becomes a
    Skill like any other: policy-checked and audited by the enclosing
    SkillServer, approval-gateable if its side_effects is unsafe_write.

    `transport` is exposed only for tests (httpx.MockTransport) — leave it
    unset in production to make real network calls.
    """

    def __init__(
        self, webhook_skills: list[WebhookSkill], *,
        endpoint: str, name: str, department: str,
        version: str = '1.0.0', identity_ref: str | None = None,
        transport: 'httpx.BaseTransport | None' = None,
    ) -> None:
        self._by_id = {ws.skill.skill_id: ws for ws in webhook_skills}
        self._name = name
        self._department = department
        self._version = version
        self._endpoint = endpoint
        self._identity_ref = identity_ref or f'webhook-{name}'
        self._transport = transport

    def describe(self) -> AgentManifest:
        return Agent.manifest(
            name=self._name, framework='webhook', department=self._department,
            version=self._version, endpoint=self._endpoint, identity_ref=self._identity_ref,
            skills=[ws.skill for ws in self._by_id.values()],
        )

    async def invoke(self, skill_id: str, payload: dict) -> dict:
        webhook = self._by_id[skill_id]
        httpx = _import_httpx()
        async with httpx.AsyncClient(transport=self._transport, timeout=webhook.timeout_seconds) as client:
            response = await client.request(
                webhook.method, webhook.url.format(payload=payload), json=payload, headers=webhook.headers,
            )
        response.raise_for_status()
        return response.json()
