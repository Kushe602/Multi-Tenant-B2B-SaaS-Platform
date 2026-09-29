"""Gateway auth dependencies and session-cookie helpers.

The gateway validates the JWT session itself (for routing and the UI) and then
forwards the same token downstream. Cookies are ``HttpOnly`` + ``SameSite`` and
``Secure`` is configurable (enable it behind TLS in production).
"""

from __future__ import annotations

from fastapi import HTTPException, Request, Response, status

from shared.security import Principal, TokenError, decode_token


def get_token(request: Request) -> str | None:
    """Read the session token from the cookie, falling back to a Bearer header."""
    settings = request.app.state.settings
    token = request.cookies.get(settings.cookie_name)
    if token:
        return token
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return None


def get_principal_optional(request: Request) -> Principal | None:
    token = get_token(request)
    if not token:
        return None
    settings = request.app.state.settings
    try:
        return decode_token(
            token, secret_key=settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
    except TokenError:
        return None


def require_principal(request: Request) -> Principal:
    principal = get_principal_optional(request)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.jwt_expire_minutes * 60,
        httponly=True,
        samesite=settings.cookie_samesite,
        secure=settings.cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response, request: Request) -> None:
    settings = request.app.state.settings
    response.delete_cookie(settings.cookie_name, path="/")
