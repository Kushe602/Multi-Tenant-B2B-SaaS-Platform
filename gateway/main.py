"""gateway ASGI application factory -- the only publicly-exposed service.

The gateway is stateless (no database): it validates the JWT session cookie,
renders the HTMX web UI and proxies the public REST API to the internal
services over HTTP. Horizontal scaling is therefore trivial.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from .config import GatewaySettings, get_gateway_settings
from .routes_api import router as api_router
from .routes_web import router as web_router


def create_app(settings: GatewaySettings | None = None) -> FastAPI:
    settings = settings or get_gateway_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.http_client = httpx.AsyncClient(timeout=settings.request_timeout)
        yield
        await app.state.http_client.aclose()

    app = FastAPI(
        title="TenantForge gateway",
        version="1.0.0",
        summary="Public edge: web UI + REST API; validates sessions and fans out to services.",
        lifespan=lifespan,
    )
    app.state.settings = settings

    @app.get("/health", tags=["ops"])
    async def health() -> dict:
        return {"status": "ok", "service": settings.service_name}

    app.include_router(web_router)
    app.include_router(api_router)
    return app


app = create_app()
