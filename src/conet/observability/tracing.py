from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.grpc import GrpcAioInstrumentorClient, GrpcAioInstrumentorServer
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from conet.persistence.store import Store
from conet.sdk.manifests import AuditEvent, AuditOutcome

# NFR-012: audit metadata must never carry secrets or raw prompts.
_REDACT_KEYS = {'prompt', 'secret', 'password', 'token', 'api_key', 'credential'}

_grpc_client_instrumentor = GrpcAioInstrumentorClient()
_grpc_server_instrumentor = GrpcAioInstrumentorServer()


def setup_tracing(app=None, service_name: str = 'conet') -> None:
    """Wire up one shared trace context across FastAPI, internal calls, and gRPC.

    Safe to call more than once — each instrumentor tracks its own
    already-instrumented state and no-ops on repeat calls.
    """
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        provider = TracerProvider(resource=Resource.create({'service.name': service_name}))
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)

    if not _grpc_client_instrumentor.is_instrumented_by_opentelemetry:
        _grpc_client_instrumentor.instrument()
    if not _grpc_server_instrumentor.is_instrumented_by_opentelemetry:
        _grpc_server_instrumentor.instrument()

    if app is not None:
        FastAPIInstrumentor.instrument_app(app)


def _redact(metadata: dict) -> dict:
    return {k: ('***REDACTED***' if k.lower() in _REDACT_KEYS else v) for k, v in metadata.items()}


async def audit(
    store: Store,
    actor: str,
    action: str,
    resource: str,
    outcome: AuditOutcome,
    trace_id: str | None = None,
    metadata: dict | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor=actor, action=action, resource=resource, outcome=outcome,
        trace_id=trace_id, metadata=_redact(metadata or {}),
    )
    await store.append_audit(event)
    return event
