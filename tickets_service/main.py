"""tickets-service ASGI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from shared.runtime import dispose_state, init_state

from .config import TicketsSettings, get_tickets_settings
from .models import Base
from .routes import router


def create_app(settings: TicketsSettings | None = None) -> FastAPI:
    settings = settings or get_tickets_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await init_state(app, settings, Base.metadata)
        yield
        await dispose_state(app)

    app = FastAPI(
        title="TenantForge tickets-service",
        version="1.0.0",
        summary="Owns tickets and comments; strictly tenant-scoped; emits ticket events.",
        lifespan=lifespan,
    )
    app.state.settings = settings

    @app.get("/health", tags=["ops"])
    async def health() -> dict:
        return {"status": "ok", "service": settings.service_name}

    app.include_router(router)
    return app


app = create_app()
