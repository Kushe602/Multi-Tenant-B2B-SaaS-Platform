"""notifications-service ASGI application factory (API + background consumer)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI

from shared.runtime import dispose_state, init_state

from .config import NotificationsSettings, get_notifications_settings
from .consumer import register_subscriptions
from .models import Base
from .routes import router


def create_app(settings: NotificationsSettings | None = None) -> FastAPI:
    settings = settings or get_notifications_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Wire the event subscriptions before the bus consumer starts.
        on_bus_ready = partial(register_subscriptions, email_demo=settings.email_demo_mode)
        await init_state(app, settings, Base.metadata, on_bus_ready=on_bus_ready)
        yield
        await dispose_state(app)

    app = FastAPI(
        title="TenantForge notifications-service",
        version="1.0.0",
        summary="Consumes ticket.* events into tenant-scoped in-app notifications.",
        lifespan=lifespan,
    )
    app.state.settings = settings

    @app.get("/health", tags=["ops"])
    async def health() -> dict:
        return {"status": "ok", "service": settings.service_name}

    app.include_router(router)
    return app


app = create_app()
