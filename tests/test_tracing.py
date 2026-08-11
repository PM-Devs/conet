import pytest
from fastapi import FastAPI

from conet.observability.tracing import audit, setup_tracing
from conet.persistence.store import Store


@pytest.fixture
async def store():
    s = Store(':memory:')
    yield s
    await s.close()


def test_setup_tracing_is_idempotent():
    app = FastAPI()
    setup_tracing(app)
    setup_tracing(app)  # must not raise on a second call


async def test_audit_persists_event_and_returns_it(store):
    event = await audit(store, actor='agent-a', action='invoke', resource='invoice.verify', outcome='OK')
    assert event.actor == 'agent-a'
    assert event.outcome == 'OK'


async def test_audit_redacts_sensitive_metadata_keys(store):
    event = await audit(
        store, actor='agent-a', action='invoke', resource='invoice.verify', outcome='OK',
        metadata={'api_key': 'sk-super-secret', 'invoice_id': 'INV-1'},
    )
    assert event.metadata['api_key'] == '***REDACTED***'
    assert event.metadata['invoice_id'] == 'INV-1'


async def test_audit_carries_trace_id_through(store):
    event = await audit(
        store, actor='agent-a', action='invoke', resource='invoice.verify',
        outcome='DENIED', trace_id='trace-123',
    )
    assert event.trace_id == 'trace-123'
