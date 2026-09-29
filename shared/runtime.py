"""Shared lifespan/runtime helpers so each service's ``main.py`` stays tiny.

``init_state`` performs the common startup sequence (engine -> create tables ->
event bus -> optional subscriptions) and stores everything on ``app.state`` where
the shared dependencies expect to find it. ``dispose_state`` tears it down.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

from fastapi import FastAPI
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.config import BaseServiceSettings
from shared.database import make_engine, make_sessionmaker
from shared.events import EventBus, build_event_bus

# Callback that wires subscriptions before the bus consumer starts.
OnBusReady = Callable[[EventBus, "async_sessionmaker[AsyncSession]"], Awaitable[None]]


async def init_state(
    app: FastAPI,
    settings: BaseServiceSettings,
    metadata: MetaData,
    *,
    on_bus_ready: Optional[OnBusReady] = None,
) -> None:
    engine = make_engine(settings.resolved_database_url)
    sessionmaker = make_sessionmaker(engine)

    # Demo mode auto-creates the schema; a real deployment would use migrations.
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    bus = build_event_bus(settings.redis_url)
    if on_bus_ready is not None:
        # Register subscriptions before start() so the Redis consumer subscribes
        # to exactly the right channels.
        await on_bus_ready(bus, sessionmaker)
    await bus.start()

    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    app.state.event_bus = bus


async def dispose_state(app: FastAPI) -> None:
    bus = getattr(app.state, "event_bus", None)
    if bus is not None:
        await bus.close()
    engine = getattr(app.state, "engine", None)
    if engine is not None:
        await engine.dispose()
