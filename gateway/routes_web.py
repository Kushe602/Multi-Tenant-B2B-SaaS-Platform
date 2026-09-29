"""Server-rendered web UI (Jinja2 + HTMX). All state lives in the services;
these routes just orchestrate downstream calls and render fragments.
"""

from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import clients
from .deps import (
    clear_session_cookie,
    get_principal_optional,
    get_token,
    set_session_cookie,
)

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _redirect_to_login() -> Response:
    """HTMX-aware redirect: works for both full-page and hx-request calls."""
    resp = HTMLResponse("", status_code=status.HTTP_401_UNAUTHORIZED)
    resp.headers["HX-Redirect"] = "/login"
    return resp


def _json_or_none(resp: httpx.Response):
    try:
        return resp.json()
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Public pages / auth
# --------------------------------------------------------------------------- #
@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> Response:
    if get_principal_optional(request) is not None:
        return RedirectResponse("/app", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "index.html")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> Response:
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
) -> Response:
    resp = await clients.identity(
        request, "POST", "/auth/login", json={"email": email, "password": password}
    )
    if resp.status_code >= 300:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid email or password.", "email": email},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    out = RedirectResponse("/app", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(out, request, resp.json()["access_token"])
    return out


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request) -> Response:
    return templates.TemplateResponse(request, "signup.html", {"error": None})


@router.post("/signup", response_class=HTMLResponse)
async def signup_submit(
    request: Request,
    org_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    name: str = Form(""),
) -> Response:
    resp = await clients.identity(
        request,
        "POST",
        "/auth/signup",
        json={"org_name": org_name, "email": email, "password": password, "name": name},
    )
    if resp.status_code >= 300:
        data = _json_or_none(resp) or {}
        detail = data.get("detail")
        message = (
            detail if isinstance(detail, str) else "Could not create account. Check your details."
        )
        return templates.TemplateResponse(
            request,
            "signup.html",
            {"error": message, "org_name": org_name, "email": email, "name": name},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    out = RedirectResponse("/app", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(out, request, resp.json()["access_token"])
    return out


@router.post("/logout")
async def logout(request: Request) -> Response:
    out = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(out, request)
    return out


# --------------------------------------------------------------------------- #
# Authenticated dashboard
# --------------------------------------------------------------------------- #
@router.get("/app", response_class=HTMLResponse)
async def dashboard(request: Request) -> Response:
    principal = get_principal_optional(request)
    if principal is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "app.html", {"principal": principal})


@router.get("/app/tickets", response_class=HTMLResponse)
async def tickets_partial(request: Request) -> Response:
    principal = get_principal_optional(request)
    if principal is None:
        return _redirect_to_login()
    resp = await clients.tickets(
        request, "GET", "/tickets", token=get_token(request), params=dict(request.query_params)
    )
    tickets = _json_or_none(resp) or []
    return templates.TemplateResponse(
        request, "partials/tickets.html", {"tickets": tickets, "principal": principal}
    )


@router.post("/app/tickets", response_class=HTMLResponse)
async def create_ticket_web(
    request: Request,
    subject: str = Form(...),
    description: str = Form(""),
    priority: str = Form("normal"),
) -> Response:
    principal = get_principal_optional(request)
    if principal is None:
        return _redirect_to_login()
    await clients.tickets(
        request,
        "POST",
        "/tickets",
        token=get_token(request),
        json={"subject": subject, "description": description, "priority": priority},
    )
    resp = await clients.tickets(request, "GET", "/tickets", token=get_token(request))
    tickets = _json_or_none(resp) or []
    out = templates.TemplateResponse(
        request, "partials/tickets.html", {"tickets": tickets, "principal": principal}
    )
    # Nudge the notifications panel to refresh (a ticket.created event fired).
    out.headers["HX-Trigger"] = "ticketsChanged"
    return out


@router.get("/app/tickets/{ticket_id}", response_class=HTMLResponse)
async def ticket_detail(request: Request, ticket_id: str) -> Response:
    principal = get_principal_optional(request)
    if principal is None:
        return _redirect_to_login()
    token = get_token(request)
    tresp = await clients.tickets(request, "GET", f"/tickets/{ticket_id}", token=token)
    if tresp.status_code == 404:
        return HTMLResponse(
            '<div class="text-sm text-rose-600">Ticket not found in your workspace.</div>',
            status_code=status.HTTP_404_NOT_FOUND,
        )
    ticket = _json_or_none(tresp)
    cresp = await clients.tickets(request, "GET", f"/tickets/{ticket_id}/comments", token=token)
    comments = _json_or_none(cresp) or []
    return templates.TemplateResponse(
        request,
        "partials/ticket_detail.html",
        {"ticket": ticket, "comments": comments, "principal": principal},
    )


@router.post("/app/tickets/{ticket_id}/status", response_class=HTMLResponse)
async def update_status_web(
    request: Request, ticket_id: str, status_value: str = Form(..., alias="status")
) -> Response:
    principal = get_principal_optional(request)
    if principal is None:
        return _redirect_to_login()
    token = get_token(request)
    await clients.tickets(
        request, "PATCH", f"/tickets/{ticket_id}", token=token, json={"status": status_value}
    )
    return await ticket_detail(request, ticket_id)


@router.post("/app/tickets/{ticket_id}/comments", response_class=HTMLResponse)
async def add_comment_web(
    request: Request, ticket_id: str, body: str = Form(...)
) -> Response:
    principal = get_principal_optional(request)
    if principal is None:
        return _redirect_to_login()
    await clients.tickets(
        request,
        "POST",
        f"/tickets/{ticket_id}/comments",
        token=get_token(request),
        json={"body": body},
    )
    return await ticket_detail(request, ticket_id)


@router.get("/app/notifications", response_class=HTMLResponse)
async def notifications_partial(request: Request) -> Response:
    principal = get_principal_optional(request)
    if principal is None:
        return _redirect_to_login()
    resp = await clients.notifications(
        request, "GET", "/notifications", token=get_token(request), params={"limit": "15"}
    )
    items = _json_or_none(resp) or []
    return templates.TemplateResponse(
        request, "partials/notifications.html", {"notifications": items}
    )
