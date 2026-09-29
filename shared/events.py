"""Event bus abstraction for cross-service events (e.g. ``ticket.created``).

Two interchangeable implementations behind one interface:

* :class:`InMemoryEventBus` -- zero-dependency, in-process, *synchronous*
  delivery. Used automatically whenever ``REDIS_URL`` is unset (demo mode and
  the whole test-suite). Because delivery is inline, tests are deterministic:
  once ``publish`` returns, every subscriber has run.
* :class:`RedisEventBus` -- real Redis Pub/Sub with a background consumer task,
  used in production / docker-compose. This is what lets services scale out and
  communicate across processes and hosts.

Handlers are ``async def handler(payload: dict) -> None``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Awaitable, Callable, Protocol, runtime_checkable

logger = logging.getLogger("tenantforge.events")

Handler = Callable[[dict], Awaitable[None]]


@runtime_checkable
class EventBus(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def publish(self, topic: str, payload: dict) -> None: ...
    async def subscribe(self, topic: str, handler: Handler) -> None: ...


class InMemoryEventBus:
    """In-process bus with synchronous fan-out. Great for tests and demos."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}
        # Recorded (topic, payload) pairs -- handy for assertions in tests.
        self.published: list[tuple[str, dict]] = []

    async def start(self) -> None:  # noqa: D401 - nothing to warm up
        return None

    async def close(self) -> None:
        self._handlers.clear()

    async def subscribe(self, topic: str, handler: Handler) -> None:
        self._handlers.setdefault(topic, []).append(handler)

    async def publish(self, topic: str, payload: dict) -> None:
        self.published.append((topic, payload))
        for handler in list(self._handlers.get(topic, [])):
            try:
                await handler(payload)
            except Exception:  # a bad subscriber must not break the publisher
                logger.exception("in-memory subscriber for %s failed", topic)


class RedisEventBus:
    """Redis Pub/Sub bus with a single background consumer task."""

    def __init__(self, url: str, *, channel_prefix: str = "tf") -> None:
        import redis.asyncio as aioredis  # local import: optional in demo mode

        self._redis = aioredis.from_url(url, decode_responses=True)
        self._prefix = channel_prefix
        self._handlers: dict[str, list[Handler]] = {}
        self._pubsub = None
        self._task: asyncio.Task | None = None

    def _channel(self, topic: str) -> str:
        return f"{self._prefix}:{topic}"

    async def start(self) -> None:
        if not self._handlers:
            return
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(*[self._channel(t) for t in self._handlers])
        self._task = asyncio.create_task(self._consume(), name="tf-event-consumer")

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._pubsub is not None:
            await self._pubsub.aclose()
        await self._redis.aclose()

    async def subscribe(self, topic: str, handler: Handler) -> None:
        self._handlers.setdefault(topic, []).append(handler)

    async def publish(self, topic: str, payload: dict) -> None:
        await self._redis.publish(self._channel(topic), json.dumps(payload))

    async def _consume(self) -> None:
        assert self._pubsub is not None
        channel_to_topic = {self._channel(t): t for t in self._handlers}
        async for message in self._pubsub.listen():
            if message.get("type") != "message":
                continue
            topic = channel_to_topic.get(message["channel"])
            if topic is None:
                continue
            try:
                payload = json.loads(message["data"])
            except (TypeError, ValueError):
                logger.warning("dropping non-JSON message on %s", topic)
                continue
            for handler in list(self._handlers.get(topic, [])):
                try:
                    await handler(payload)
                except Exception:
                    logger.exception("redis subscriber for %s failed", topic)


def build_event_bus(redis_url: str | None) -> EventBus:
    """Factory: Redis bus when a URL is provided, else the in-memory bus."""
    if redis_url:
        return RedisEventBus(redis_url)
    return InMemoryEventBus()
