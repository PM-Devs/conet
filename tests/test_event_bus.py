import asyncio

import pytest
from conftest import requires_nats

from conet.protocols.events.bus import EventBus

pytestmark = requires_nats


@pytest.fixture
async def bus():
    b = EventBus('nats://localhost:4222')
    yield b
    # tests reuse the durable stream/consumer names, so reset server-side
    # state between runs rather than leaving it to bleed into the next run
    _, js = await b._connect()
    try:
        await js.delete_stream('CONET')
    except Exception:  # noqa: BLE001, S110 — best-effort teardown; a missing stream is not a test failure
        pass
    await b.close()


async def test_core_subject_drops_events_published_before_subscribe(bus):
    for i in range(5):
        await bus.publish('conet.health.ping', {'i': i})
    await asyncio.sleep(0.2)

    received = []
    await bus.subscribe('conet.health.ping', lambda payload: received.append(payload))
    await asyncio.sleep(0.3)

    assert received == []


async def test_durable_subject_replays_to_a_late_subscriber(bus):
    subject = 'conet.agent.registered'
    for i in range(5):
        await bus.publish(subject, {'i': i})
    await asyncio.sleep(0.2)

    received = []
    event = asyncio.Event()

    async def handler(payload):
        received.append(payload)
        if len(received) == 5:
            event.set()

    await bus.subscribe(subject, handler)
    await asyncio.wait_for(event.wait(), timeout=3)

    assert sorted(p['i'] for p in received) == [0, 1, 2, 3, 4]
