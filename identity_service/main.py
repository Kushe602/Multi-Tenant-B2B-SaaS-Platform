"""identity-service ASGI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from shared.runtime import dispose_state, init_state

from .config import IdentitySettings, get_identity_settings
from .models import Base
from .routes import router


def create_app(settings: IdentitySettings | None = None) -> FastAPI:
    settings = settings or get_identity_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await init_state(app, settings, Base.metadata)
        yield
        await dispose_state(app)

    app = FastAPI(
        title="TenantForge identity-service",
        version="1.0.0",
        summary="Owns tenants, users, authentication (bcrypt + JWT), RBAC and invites.",
        lifespan=lifespan,
    )
    # Make settings available to dependencies even before lifespan runs
    # (the test-suite drives the app without executing the lifespan).
    app.state.settings = settings

    @app.get("/health", tags=["ops"])
    async def health() -> dict:
        return {"status": "ok", "service": settings.service_name}

    app.include_router(router)
    return app


app = create_app()
