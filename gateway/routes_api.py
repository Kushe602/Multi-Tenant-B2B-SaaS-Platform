"""Public JSON REST API exposed by the gateway (prefix ``/api``).

These endpoints are a thin, authenticated proxy over the internal services. Auth
endpoints additionally set the ``HttpOnly`` session cookie so the same token can
drive the HTMX web UI.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, Request, Response, status
from fastapi.responses import JSONResponse

from shared.security import Principal

from . import clients
from .deps import get_token, require_principal, set_session_cookie

router = APIRouter(prefix="/api", tags=["api"])


def _passthrough(resp: httpx.Response) -> JSONResponse:
    try:
        content: Any = resp.json()
    except ValueError:
        content = {"detail": resp.text}
    return JSONResponse(content=content, status_code=resp.status_code)


async def _is_tenant_user(request: Request, user_id: str) -> bool:
    """True if ``user_id`` belongs to the caller's tenant (per the identity service).

    ``/users`` is itself tenant-scoped from the caller's JWT, so this can only
    ever confirm membership within the caller's own tenant.
    """
    resp = await clients.identity(request, "GET", "/users", token=get_token(request))
    if resp.status_code >= 300:
        return False
    try:
        users = resp.json()
    except ValueError:
        return False
    return any(isinstance(u, dict) and u.get("id") == user_id for u in users)


async def _auth_and_set_cookie(request: Request, path: str, payload: dict) -> Response:
    resp = await clients.identity(request, "POST", path, json=payload)
    out = _passthrough(resp)
    if resp.status_code < 300:
        token = resp.json().get("access_token")
        if token:
            set_session_cookie(out, request, token)
    return out


@router.post("/auth/signup")
async def api_signup(request: Request, payload: dict = Body(...)) -> Response:
    return await _auth_and_set_cookie(request, "/auth/signup", payload)


@router.post("/auth/login")
async def api_login(request: Request, payload: dict = Body(...)) -> Response:
    return await _auth_and_set_cookie(request, "/auth/login", payload)


@router.post("/auth/logout")
async def api_logout(request: Request) -> Response:
    out = JSONResponse({"status": "logged_out"})
    out.delete_cookie(request.app.state.settings.cookie_name, path="/")
    return out


@router.get("/me")
async def api_me(request: Request, principal: Principal = Depends(require_principal)) -> Response:
    return _passthrough(await clients.identity(request, "GET", "/me", token=get_token(request)))


@router.get("/users")
async def api_users(
    request: Request, principal: Principal = Depends(require_principal)
) -> Response:
    return _passthrough(await clients.identity(request, "GET", "/users", token=get_token(request)))


@router.post("/invites", status_code=status.HTTP_201_CREATED)
async def api_create_invite(
    request: Request, payload: dict = Body(...), principal: Principal = Depends(require_principal)
) -> Response:
    return _passthrough(
        await clients.identity(request, "POST", "/invites", json=payload, token=get_token(request))
    )


@router.get("/tickets")
async def api_list_tickets(
    request: Request, principal: Principal = Depends(require_principal)
) -> Response:
    return _passthrough(
        await clients.tickets(
            request, "GET", "/tickets", token=get_token(request), params=dict(request.query_params)
        )
    )


@router.post("/tickets", status_code=status.HTTP_201_CREATED)
async def api_create_ticket(
    request: Request, payload: dict = Body(...), principal: Principal = Depends(require_principal)
) -> Response:
    return _passthrough(
        await clients.tickets(request, "POST", "/tickets", json=payload, token=get_token(request))
    )


@router.get("/tickets/stats")
async def api_ticket_stats(
    request: Request, principal: Principal = Depends(require_principal)
) -> Response:
    return _passthrough(
        await clients.tickets(request, "GET", "/tickets/stats", token=get_token(request))
    )


@router.get("/tickets/{ticket_id}")
async def api_get_ticket(
    ticket_id: str, request: Request, principal: Principal = Depends(require_principal)
) -> Response:
    return _passthrough(
        await clients.tickets(request, "GET", f"/tickets/{ticket_id}", token=get_token(request))
    )


@router.patch("/tickets/{ticket_id}")
async def api_update_ticket(
    ticket_id: str,
    request: Request,
    payload: dict = Body(...),
    principal: Principal = Depends(require_principal),
) -> Response:
    return _passthrough(
        await clients.tickets(
            request, "PATCH", f"/tickets/{ticket_id}", json=payload, token=get_token(request)
        )
    )


@router.post("/tickets/{ticket_id}/assign")
async def api_assign_ticket(
    ticket_id: str,
    request: Request,
    payload: dict = Body(...),
    principal: Principal = Depends(require_principal),
) -> Response:
    assignee = payload.get("assignee_user_id")
    # Enforce "a user in the tenant": validate against identity before assigning.
    # The tickets-service cannot see identity's DB, so this cross-service check
    # lives at the gateway (which can). An unknown/foreign user id is rejected.
    if assignee and not await _is_tenant_user(request, assignee):
        return JSONResponse(
            {"detail": "assignee is not a user in this tenant"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return _passthrough(
        await clients.tickets(
            request,
            "POST",
            f"/tickets/{ticket_id}/assign",
            json={"assignee_user_id": assignee},
            token=get_token(request),
        )
    )


@router.get("/tickets/{ticket_id}/comments")
async def api_list_comments(
    ticket_id: str, request: Request, principal: Principal = Depends(require_principal)
) -> Response:
    return _passthrough(
        await clients.tickets(
            request, "GET", f"/tickets/{ticket_id}/comments", token=get_token(request)
        )
    )


@router.post("/tickets/{ticket_id}/comments", status_code=status.HTTP_201_CREATED)
async def api_add_comment(
    ticket_id: str,
    request: Request,
    payload: dict = Body(...),
    principal: Principal = Depends(require_principal),
) -> Response:
    return _passthrough(
        await clients.tickets(
            request,
            "POST",
            f"/tickets/{ticket_id}/comments",
            json=payload,
            token=get_token(request),
        )
    )


@router.get("/notifications")
async def api_notifications(
    request: Request, principal: Principal = Depends(require_principal)
) -> Response:
    return _passthrough(
        await clients.notifications(
            request,
            "GET",
            "/notifications",
            token=get_token(request),
            params=dict(request.query_params),
        )
    )


@router.post("/notifications/{notification_id}/read")
async def api_mark_read(
    notification_id: str, request: Request, principal: Principal = Depends(require_principal)
) -> Response:
    return _passthrough(
        await clients.notifications(
            request, "POST", f"/notifications/{notification_id}/read", token=get_token(request)
        )
    )
