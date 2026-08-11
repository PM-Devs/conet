import asyncio
import logging
from typing import TYPE_CHECKING

from conet.control.policy import PolicyEngine
from conet.observability.tracing import audit
from conet.persistence.store import Store
from conet.sdk.manifests import SkillDef

if TYPE_CHECKING:
    from mcp import ClientSession, StdioServerParameters

logger = logging.getLogger(__name__)


def _import_mcp():
    # mcp is an optional dependency (Feature Plan F12, Stage D) -- importing
    # it lazily, only when a gateway is actually used, means the rest of the
    # package (including conet.dashboard, which references MCPGateway for
    # its Integrations panel) stays importable without `pip install
    # conet[mcp]`.
    try:
        import mcp
    except ImportError as exc:
        raise RuntimeError(
            "mcp is not installed — it's an optional dependency: pip install 'conet[mcp]'",
        ) from exc
    return mcp


class UnknownServerError(Exception):
    """Raised when an operation targets a server_name that isn't connected."""


class UnknownCapabilityError(Exception):
    """Raised when invoke_external_capability targets a skill_id that hasn't been imported."""


class _ConnectedServer:
    """Runs one MCP connection's full lifecycle (stdio_client + ClientSession)
    in its own dedicated asyncio Task, from open to close.

    anyio (which the mcp SDK's stdio transport is built on) requires a task
    group's cancel scope to be entered and exited by the same task, in
    strict LIFO order relative to other scopes on that task. A gateway
    managing several independent connections that can each be opened and
    closed in any order — exactly what disconnect_server() promises — can't
    guarantee that if every connection shares the caller's task. Giving each
    connection its own task sidesteps the constraint entirely: each one's
    scope lives and dies on a task nobody else touches.
    """

    def __init__(self) -> None:
        self.session: ClientSession | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._error: BaseException | None = None
        self._task: asyncio.Task | None = None

    async def start(self, params: 'StdioServerParameters') -> None:
        self._task = asyncio.create_task(self._run(params))
        await self._ready.wait()
        if self._error is not None:
            raise self._error

    async def _run(self, params: 'StdioServerParameters') -> None:
        mcp = _import_mcp()
        try:
            async with mcp.stdio_client(params) as (read, write), mcp.ClientSession(read, write) as session:
                await session.initialize()
                self.session = session
                self._ready.set()
                await self._stop.wait()
        except BaseException as exc:  # noqa: BLE001 -- surfaced to start()/close() via self._error, not swallowed
            self._error = exc
            self._ready.set()

    async def close(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task

    def require_session(self) -> 'ClientSession':
        """self.session is only None before start() completes or after
        close() -- both cases are programmer errors from within this
        module, never a state a caller-visible MCPGateway method can be in."""
        assert self.session is not None, 'server not started'
        return self.session


class MCPGateway:
    """One managed boundary for external MCP tools: many external servers
    behind one governed, credential-isolated boundary (Feature Plan F12).

    Credentials never enter a Skill manifest or a trace — they live only in
    the subprocess env of the connected server, never stored or returned by
    this gateway. Proven structurally sound by Stage A's A7 prototype;
    this is the real, connection-lifecycle-managing version.
    """

    def __init__(self, policy_engine: PolicyEngine, store: Store | None = None) -> None:
        self._policy = policy_engine
        self._store = store
        self._servers: dict[str, _ConnectedServer] = {}
        self._capabilities: dict[str, tuple[str, str]] = {}  # skill_id -> (server_name, tool_name)

    async def connect_server(
        self, server_name: str, command: str, args: list[str] | None = None, env: dict[str, str] | None = None,
    ) -> None:
        params = _import_mcp().StdioServerParameters(command=command, args=args or [], env=env)
        server = _ConnectedServer()
        await server.start(params)
        self._servers[server_name] = server
        logger.info('mcp gateway: connected server=%s', server_name)

    async def disconnect_server(self, server_name: str) -> None:
        server = self._servers.pop(server_name, None)
        if server is None:
            raise UnknownServerError(f'server {server_name!r} is not connected')
        await server.close()
        for skill_id in [sid for sid, (owner, _) in self._capabilities.items() if owner == server_name]:
            del self._capabilities[skill_id]
        logger.info('mcp gateway: disconnected server=%s', server_name)

    @staticmethod
    def _namespaced_skill_id(server_name: str, tool_name: str) -> str:
        # collision-free: two servers exposing a same-named tool land at distinct skill_ids
        return f'mcp.{server_name}.{tool_name}'

    async def import_capabilities(self, server_name: str) -> list[SkillDef]:
        server = self._servers.get(server_name)
        if server is None:
            raise UnknownServerError(f'server {server_name!r} is not connected')

        tools = (await server.require_session().list_tools()).tools
        skills = []
        for tool in tools:
            skill_id = self._namespaced_skill_id(server_name, tool.name)
            self._capabilities[skill_id] = (server_name, tool.name)
            skills.append(SkillDef(
                skill_id=skill_id, version='1.0.0', summary=tool.description or '', side_effects='read_only',
                input_schema=tool.input_schema, output_schema=tool.output_schema or {'type': 'object'},
            ))
        logger.info('mcp gateway: imported %d capabilities from server=%s', len(skills), server_name)
        return skills

    async def invoke_external_capability(self, requester: str, skill_id: str, payload: dict) -> dict:
        """Passes through the same authorize + audit path internal tasks use."""
        mapping = self._capabilities.get(skill_id)
        if mapping is None:
            raise UnknownCapabilityError(f'skill_id {skill_id!r} is not an imported MCP capability')
        server_name, tool_name = mapping

        if not await self._policy.authorize(requester, skill_id, 'invoke'):
            await self._audit(requester, skill_id, 'DENIED')
            raise PermissionError(f'{requester!r} is not authorized to invoke {skill_id!r}')

        try:
            result = await self._servers[server_name].require_session().call_tool(tool_name, payload)
        except Exception:
            logger.exception('mcp gateway: call_tool failed for skill_id=%s', skill_id)
            await self._audit(requester, skill_id, 'FAILED')
            raise

        await self._audit(requester, skill_id, 'OK')
        return {'content': [block.model_dump() for block in result.content]}

    async def _audit(self, requester: str, skill_id: str, outcome) -> None:
        if self._store is not None:
            await audit(self._store, actor=requester, action=f'invoke:{skill_id}', resource=skill_id, outcome=outcome)

    async def close_all(self) -> None:
        for server_name in list(self._servers):
            await self.disconnect_server(server_name)

    def list_connected_servers(self) -> dict[str, list[str]]:
        """server_name -> imported skill_ids, for the dashboard's Integrations panel."""
        result: dict[str, list[str]] = {name: [] for name in self._servers}
        for skill_id, (server_name, _tool_name) in self._capabilities.items():
            result.setdefault(server_name, []).append(skill_id)
        return result
