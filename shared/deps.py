"""FastAPI dependencies shared by the downstream services.

State (settings / sessionmaker / event bus) is read from ``request.app.state``,
which is populated by each service's lifespan in production and by the test
fixtures under pytest. Reading from ``app.state`` (rather than module globals)
is what lets a single process host several service apps side-by-side in tests.

Authentication has two layers, enforced on every downstream endpoint:

1. ``X-Internal-Secret`` must match -- proves the request came through the
   gateway (the services are never exposed publicly).
2. A valid ``Authorization: Bearer <jwt>`` -- the tenant/user/role are derived
   from the *signed* token, never from client-supplied headers or bodies.
"""

from __future__ import annotations

import hmac
from typing import AsyncIterator, Callable

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config import BaseServiceSettings
from shared.events import EventBus
from shared.security import Principal, TokenError, decode_token


def get_settings(request: Request) -> BaseServiceSettings:
    return request.app.state.settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session


def get_event_bus(request: Request) -> EventBus:
    return request.app.state.event_bus


def require_internal(
    request: Request,
    x_internal_secret: str | None = Header(default=None),
) -> None:
    """Reject calls that do not carry the shared internal service secret."""
    expected = request.app.state.settings.internal_secret
    if not x_internal_secret or not hmac.compare_digest(x_internal_secret, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid or missing internal service secret",
        )


def get_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    _internal: None = Depends(require_internal),
) -> Principal:
    """Validate the forwarded JWT and return the authenticated principal."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    settings = request.app.state.settings
    try:
        return decode_token(
            token,
            secret_key=settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def require_role(*roles: str) -> Callable[..., Principal]:
    """Dependency factory enforcing that the principal has one of ``roles``."""

    def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        if principal.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role '{principal.role}' not permitted (requires one of {list(roles)})",
            )
        return principal

    return _dep
