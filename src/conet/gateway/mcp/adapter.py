from conet.gateway.mcp.gateway import MCPGateway
from conet.sdk.agent import Agent
from conet.sdk.manifests import AgentManifest, SkillDef


class GatewayAdapter:
    """Exposes an MCPGateway's already-imported capabilities as a normal,
    discoverable CoNET agent: run it with conet.sdk.run()/start() like any
    other adapter, and every imported MCP tool becomes reachable through
    the ordinary Router (router.execute(requester, skill_id, payload)),
    policy-checked and audited by the same SkillServer path a hand-built
    adapter's Skills use -- instead of requiring callers to hold a direct
    reference to the gateway and call invoke_external_capability().

    Credentials still never leave the gateway's own connected-server
    subprocess env; this only adds a discoverable front door to skills the
    gateway already imported.
    """

    def __init__(
        self, gateway: MCPGateway, skills: list[SkillDef], *,
        endpoint: str, name: str = 'mcp-gateway', department: str = 'integrations',
        version: str = '1.0.0', identity_ref: str = 'mcp-gateway',
    ) -> None:
        self._gateway = gateway
        self._skills = skills
        self._name = name
        self._department = department
        self._version = version
        self._endpoint = endpoint
        self._identity_ref = identity_ref

    def describe(self) -> AgentManifest:
        return Agent.manifest(
            name=self._name, framework='mcp-gateway', department=self._department,
            version=self._version, endpoint=self._endpoint, identity_ref=self._identity_ref,
            skills=self._skills,
        )

    async def invoke(self, skill_id: str, payload: dict) -> dict:
        return await self._gateway.invoke_capability_unchecked(skill_id, payload)
