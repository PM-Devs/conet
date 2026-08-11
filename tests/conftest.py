import asyncio

import nats
import pytest


def _nats_available() -> bool:
    async def _check() -> bool:
        try:
            nc = await nats.connect('nats://localhost:4222', connect_timeout=1)
            await nc.close()
            return True
        except Exception:  # noqa: BLE001 — any connection failure means "not available", regardless of cause
            return False

    return asyncio.run(_check())


requires_nats = pytest.mark.skipif(
    not _nats_available(), reason='requires a local NATS server on localhost:4222',
)
