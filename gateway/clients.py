"""Thin HTTP client helpers for calling downstream services.

Every downstream call carries the shared internal secret (proving it came from
the gateway) and, when the caller is authenticated, forwards their JWT as a
Bearer token so the downstream service can independently verify tenant/role.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import Request


def _headers(request: Request, token: str | None) -> dict[str, str]:
    settings = request.app.state.settings
    headers = {"X-Internal-Secret": settings.internal_secret}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def call_service(
    request: Request,
    base_url: str,
    method: str,
    path: str,
    *,
    token: str | None = None,
    json: Any | None = None,
    params: dict | None = None,
) -> httpx.Response:
    client: httpx.AsyncClient = request.app.state.http_client
    settings = request.app.state.settings
    url = base_url.rstrip("/") + path
    return await client.request(
        method,
        url,
        headers=_headers(request, token),
        json=json,
        params=params,
        timeout=settings.request_timeout,
    )


async def identity(request: Request, method: str, path: str, **kwargs: Any) -> httpx.Response:
    return await call_service(
        request, request.app.state.settings.identity_url, method, path, **kwargs
    )


async def tickets(request: Request, method: str, path: str, **kwargs: Any) -> httpx.Response:
    return await call_service(
        request, request.app.state.settings.tickets_url, method, path, **kwargs
    )


async def notifications(request: Request, method: str, path: str, **kwargs: Any) -> httpx.Response:
    return await call_service(
        request, request.app.state.settings.notifications_url, method, path, **kwargs
    )
