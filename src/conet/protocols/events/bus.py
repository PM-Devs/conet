import json
import logging

import nats
from nats.errors import Error as NatsError

logger = logging.getLogger(__name__)

_STREAM_NAME = 'CONET'
_STREAM_SUBJECTS = ['conet.>']
# Per ADR-010 (docs/adr-log.md): health/heartbeat traffic is fire-and-forget
# (Core NATS); everything else (agent lifecycle, task, approval, audit) must
# survive a consumer restart, so it goes through JetStream.
_CORE_ONLY_PREFIXES = ('conet.health.',)


class EventBus:
    """Publish/subscribe wrapper over nats-py, routing each subject to Core
    NATS or JetStream per the A5 durability decision."""

    def __init__(self, nats_url: str = 'nats://localhost:4222') -> None:
        self._nats_url = nats_url
        self._nc = None
        self._js = None

    async def _connect(self):
        if self._nc is None:
            self._nc = await nats.connect(self._nats_url)
            self._js = self._nc.jetstream()
            try:
                await self._js.add_stream(name=_STREAM_NAME, subjects=_STREAM_SUBJECTS)
            except NatsError:
                pass  # stream already exists with a compatible config
        return self._nc, self._js

    @staticmethod
    def _is_durable(subject: str) -> bool:
        return not subject.startswith(_CORE_ONLY_PREFIXES)

    @staticmethod
    def _durable_name(subject: str) -> str:
        return 'conet-' + subject.replace('.', '-').replace('*', 'star').replace('>', 'all')

    async def publish(self, subject: str, payload: dict) -> None:
        try:
            nc, js = await self._connect()
            data = json.dumps(payload).encode()
            if self._is_durable(subject):
                await js.publish(subject, data)
            else:
                await nc.publish(subject, data)
        except Exception:
            logger.exception('publish failed for subject %s', subject)
            raise

    async def subscribe(self, subject: str, handler) -> None:
        try:
            nc, js = await self._connect()

            async def _on_message(msg):
                await handler(json.loads(msg.data.decode()))
                if hasattr(msg, 'ack'):
                    await msg.ack()

            if self._is_durable(subject):
                await js.subscribe(subject, durable=self._durable_name(subject), cb=_on_message)
            else:
                await nc.subscribe(subject, cb=_on_message)
        except Exception:
            logger.exception('subscribe failed for subject %s', subject)
            raise

    async def close(self) -> None:
        if self._nc is not None:
            await self._nc.close()
            self._nc = None
            self._js = None
