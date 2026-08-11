import asyncio
import contextlib
import os
import signal
from typing import Protocol, runtime_checkable

from conet.control.approvals import ApprovalWorkflow
from conet.control.policy import PolicyEngine
from conet.control.registry import Registry
from conet.persistence.store import Store
from conet.protocols.events.bus import EventBus
from conet.runtime.server import serve
from conet.sdk.manifests import AgentManifest, SkillDef


@runtime_checkable
class CoNETAdapter(Protocol):
    """The full NIC contract an agent author implements (LLD-01 §4).
    Only describe() and invoke() are required."""

    def describe(self) -> AgentManifest: ...

    async def invoke(self, skill_id: str, payload: dict) -> dict: ...

    async def stream(self, skill_id: str, payload: dict):  # -> AsyncIterator[dict]
        ...

    async def on_cancel(self, task_id: str) -> None: ...


class Agent:
    @staticmethod
    def manifest(name: str, framework: str, department: str, skills: list[SkillDef], **kw) -> AgentManifest:
        return AgentManifest(name=name, framework=framework, department=department, skills=skills, **kw)


def _endpoint_port(endpoint: str) -> int:
    return int(endpoint.rsplit(':', 1)[-1])


def _resolve(value: str | None, env_var: str, default: str) -> str:
    fallback = os.environ.get(env_var, default)
    return value or fallback


async def _renew_loop(registry: Registry, agent_id: str, interval_seconds: float) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        await registry.renew_lease(agent_id)


class RunningAgent:
    """A handle on everything `start()` brought up, so it can be torn down
    cleanly — used directly by tests, and by run_async() for real usage."""

    def __init__(self, agent_id, store, event_bus, registry, grpc_server, renew_task) -> None:
        self.agent_id = agent_id
        self.store = store
        self.event_bus = event_bus
        self.registry = registry
        self.grpc_server = grpc_server
        self._renew_task = renew_task

    async def stop(self) -> None:
        self._renew_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._renew_task
        await self.grpc_server.stop(grace=1)
        await self.registry.unregister_agent(self.agent_id)
        await self.event_bus.close()
        await self.store.close()


async def start(
    adapter: CoNETAdapter,
    *,
    db_path: str | None = None,
    nats_url: str | None = None,
    policy_secret: str | None = None,
    policy_path: str | None = None,
    approvers: list[str] | None = None,
    approval_ttl_seconds: int = 3600,
    approval_poll_interval_seconds: float = 1.0,
) -> RunningAgent:
    """Registers the agent, starts lease renewal, and starts the SkillServer.
    Returns a handle the caller must eventually .stop().

    Pass approvers (e.g. ["finance-manager@example.com"]) to gate every
    unsafe_write Skill this agent serves behind the human approval queue
    (F7) before invoke() runs — nothing else about the adapter needs to
    change. Omit it and unsafe_write Skills execute immediately, exactly as
    before.
    """
    manifest = adapter.describe()

    store = Store(_resolve(db_path, 'CONET_DB_PATH', 'conet.db'))
    event_bus = EventBus(_resolve(nats_url, 'CONET_NATS_URL', 'nats://localhost:4222'))
    policy_engine = PolicyEngine(
        secret_key=_resolve(policy_secret, 'CONET_POLICY_SECRET', 'dev-secret-change-me'),
        policy_path=policy_path or os.environ.get('CONET_POLICY_PATH'),
    )
    registry = Registry(store, event_bus)
    approvals = ApprovalWorkflow(store) if approvers else None

    agent_id = await registry.register_agent(manifest)
    renew_task = asyncio.create_task(_renew_loop(registry, agent_id, manifest.lease_ttl_seconds / 2))
    grpc_server = await serve(
        manifest, adapter, policy_engine, port=_endpoint_port(manifest.endpoint), store=store,
        approvals=approvals, approvers=approvers,
        approval_ttl_seconds=approval_ttl_seconds, approval_poll_interval_seconds=approval_poll_interval_seconds,
    )

    return RunningAgent(agent_id, store, event_bus, registry, grpc_server, renew_task)


async def run_async(adapter: CoNETAdapter, **kwargs) -> None:
    """The async form of run() — starts the agent and blocks until SIGINT/SIGTERM
    (POSIX only; on Windows, stop the process directly or call start()/.stop() yourself)."""
    running = await start(adapter, **kwargs)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError):
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)

    await stop_event.wait()
    await running.stop()


def run(adapter: CoNETAdapter, **kwargs) -> None:
    """The one call an agent author makes."""
    asyncio.run(run_async(adapter, **kwargs))
