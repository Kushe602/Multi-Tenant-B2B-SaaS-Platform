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
async def _fetch_users(request: Request, token: str | None) -> list[dict]:
    """Tenant-scoped users (for assignee pickers). Empty list on any failure."""
    resp = await clients.identity(request, "GET", "/users", token=token)
    users = _json_or_none(resp) or []
    return users if isinstance(users, list) else []


def _user_label(users: list[dict], user_id: str | None) -> str | None:
    """Human label (name or email) for an assignee id, if we know the user."""
    if not user_id:
        return None
    for u in users:
        if u.get("id") == user_id:
            return u.get("name") or u.get("email") or user_id[:8]
    return user_id[:8]


def _comment_tree(comments: list[dict]) -> list[dict]:
    """Turn a flat, chronologically-ordered comment list into a reply tree."""
    nodes = {c["id"]: {**c, "children": []} for c in comments}
    roots: list[dict] = []
    for c in comments:
        node = nodes[c["id"]]
        parent_id = c.get("parent_id")
        parent = nodes.get(parent_id) if parent_id else None
        if parent is not None:
            parent["children"].append(node)
        else:
            roots.append(node)
    return roots


@router.get("/app", response_class=HTMLResponse)
async def dashboard(request: Request) -> Response:
    principal = get_principal_optional(request)
    if principal is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    users = await _fetch_users(request, get_token(request))
    return templates.TemplateResponse(
        request, "app.html", {"principal": principal, "users": users}
    )


@router.get("/app/stats", response_class=HTMLResponse)
async def stats_partial(request: Request) -> Response:
    principal = get_principal_optional(request)
    if principal is None:
        return _redirect_to_login()
    resp = await clients.tickets(request, "GET", "/tickets/stats", token=get_token(request))
    stats = _json_or_none(resp) or {"total": 0, "by_status": {}, "by_priority": {}}
    return templates.TemplateResponse(request, "partials/stats.html", {"stats": stats})


@router.get("/app/tickets", response_class=HTMLResponse)
async def tickets_partial(request: Request) -> Response:
    principal = get_principal_optional(request)
    if principal is None:
        return _redirect_to_login()
    users = await _fetch_users(request, get_token(request))
    resp = await clients.tickets(
        request, "GET", "/tickets", token=get_token(request), params=dict(request.query_params)
    )
    tickets = _json_or_none(resp) or []
    for t in tickets:
        t["assignee_label"] = _user_label(users, t.get("assignee_user_id"))
    return templates.TemplateResponse(
        request,
        "partials/tickets.html",
        {"tickets": tickets, "principal": principal, "filters": dict(request.query_params)},
    )


@router.post("/app/tickets", response_class=HTMLResponse)
async def create_ticket_web(
    request: Request,
    subject: str = Form(...),
    description: str = Form(""),
    priority: str = Form("normal"),
    category: str = Form(""),
    tags: str = Form(""),
) -> Response:
    principal = get_principal_optional(request)
    if principal is None:
        return _redirect_to_login()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    await clients.tickets(
        request,
        "POST",
        "/tickets",
        token=get_token(request),
        json={
            "subject": subject,
            "description": description,
            "priority": priority,
            "category": category or None,
            "tags": tag_list,
        },
    )
    users = await _fetch_users(request, get_token(request))
    resp = await clients.tickets(request, "GET", "/tickets", token=get_token(request))
    tickets = _json_or_none(resp) or []
    for t in tickets:
        t["assignee_label"] = _user_label(users, t.get("assignee_user_id"))
    out = templates.TemplateResponse(
        request,
        "partials/tickets.html",
        {"tickets": tickets, "principal": principal, "filters": {}},
    )
    # Nudge the notifications + stats panels to refresh (a ticket.created event fired).
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
    users = await _fetch_users(request, token)
    if ticket is not None:
        ticket["assignee_label"] = _user_label(users, ticket.get("assignee_user_id"))
    cresp = await clients.tickets(request, "GET", f"/tickets/{ticket_id}/comments", token=token)
    comments = _json_or_none(cresp) or []
    return templates.TemplateResponse(
        request,
        "partials/ticket_detail.html",
        {
            "ticket": ticket,
            "comments": _comment_tree(comments),
            "users": users,
            "principal": principal,
        },
    )


@router.post("/app/tickets/{ticket_id}/workflow", response_class=HTMLResponse)
async def update_workflow_web(
    request: Request,
    ticket_id: str,
    status_value: str = Form(..., alias="status"),
    priority: str = Form(...),
    category: str = Form(""),
) -> Response:
    principal = get_principal_optional(request)
    if principal is None:
        return _redirect_to_login()
    await clients.tickets(
        request,
        "PATCH",
        f"/tickets/{ticket_id}",
        token=get_token(request),
        json={"status": status_value, "priority": priority, "category": category or None},
    )
    out = await ticket_detail(request, ticket_id)
    out.headers["HX-Trigger"] = "ticketsChanged"
    return out


@router.post("/app/tickets/{ticket_id}/assign", response_class=HTMLResponse)
async def assign_ticket_web(
    request: Request, ticket_id: str, assignee_user_id: str = Form("")
) -> Response:
    principal = get_principal_optional(request)
    if principal is None:
        return _redirect_to_login()
    # The assignee dropdown is populated from this tenant's users, and the ticket
    # itself is tenant-scoped downstream, so a foreign user can't be selected here.
    await clients.tickets(
        request,
        "POST",
        f"/tickets/{ticket_id}/assign",
        token=get_token(request),
        json={"assignee_user_id": assignee_user_id or None},
    )
    out = await ticket_detail(request, ticket_id)
    out.headers["HX-Trigger"] = "ticketsChanged"
    return out


@router.post("/app/tickets/{ticket_id}/comments", response_class=HTMLResponse)
async def add_comment_web(
    request: Request, ticket_id: str, body: str = Form(...), parent_id: str = Form("")
) -> Response:
    principal = get_principal_optional(request)
    if principal is None:
        return _redirect_to_login()
    await clients.tickets(
        request,
        "POST",
        f"/tickets/{ticket_id}/comments",
        token=get_token(request),
        json={"body": body, "parent_id": parent_id or None},
    )
    out = await ticket_detail(request, ticket_id)
    out.headers["HX-Trigger"] = "ticketsChanged"
    return out


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
