"""Pytest fixtures: build every service in-process with in-memory SQLite and a
shared in-memory event bus -- no PostgreSQL, Redis or Docker required.

A single ``system`` fixture wires the four apps together (the gateway talks to
identity/tickets/notifications through a custom httpx transport that dispatches
by hostname to the in-process ASGI apps), so both per-service unit tests and
full cross-service integration tests can share it.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest_asyncio
from httpx import ASGITransport

from gateway.config import GatewaySettings
from gateway.main import create_app as create_gateway_app
from identity_service.config import IdentitySettings
from identity_service.main import create_app as create_identity_app
from identity_service.models import Base as IdentityBase
from notifications_service.config import NotificationsSettings
from notifications_service.consumer import register_subscriptions
from notifications_service.main import create_app as create_notifications_app
from notifications_service.models import Base as NotifBase
from shared.database import make_engine, make_sessionmaker
from shared.events import InMemoryEventBus
from shared.security import create_access_token
from tickets_service.config import TicketsSettings
from tickets_service.main import create_app as create_tickets_app
from tickets_service.models import Base as TicketsBase

SECRET = "test-secret-key-0123456789-abcdefghij-0123456789"
INTERNAL = "test-internal-secret-0123456789-abcdefghij"


def internal_headers() -> dict:
    return {"X-Internal-Secret": INTERNAL}


def auth_headers(token: str) -> dict:
    return {"X-Internal-Secret": INTERNAL, "Authorization": f"Bearer {token}"}


class _DispatchTransport(httpx.AsyncBaseTransport):
    """Route outbound gateway requests to the right in-process ASGI app."""

    def __init__(self, mapping: dict[str, ASGITransport]) -> None:
        self._mapping = mapping

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        transport = self._mapping[request.url.host]
        return await transport.handle_async_request(request)


async def _wire(app, base, bus) -> None:
    engine = make_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(base.metadata.create_all)
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    app.state.event_bus = bus


@pytest_asyncio.fixture
async def system():
    bus = InMemoryEventBus()

    identity = create_identity_app(IdentitySettings(secret_key=SECRET, internal_secret=INTERNAL))
    tickets = create_tickets_app(TicketsSettings(secret_key=SECRET, internal_secret=INTERNAL))
    notifications = create_notifications_app(
        NotificationsSettings(secret_key=SECRET, internal_secret=INTERNAL)
    )

    await _wire(identity, IdentityBase, bus)
    await _wire(tickets, TicketsBase, bus)
    await _wire(notifications, NotifBase, bus)
    # Subscribe the notifications consumer to the shared bus.
    await register_subscriptions(bus, notifications.state.sessionmaker, email_demo=True)

    id_t = ASGITransport(app=identity)
    tk_t = ASGITransport(app=tickets)
    nt_t = ASGITransport(app=notifications)

    gateway = create_gateway_app(
        GatewaySettings(
            secret_key=SECRET,
            internal_secret=INTERNAL,
            identity_url="http://identity",
            tickets_url="http://tickets",
            notifications_url="http://notifications",
        )
    )
    gateway.state.http_client = httpx.AsyncClient(
        transport=_DispatchTransport({"identity": id_t, "tickets": tk_t, "notifications": nt_t}),
        base_url="http://internal",
    )
    gw_t = ASGITransport(app=gateway)

    clients = SimpleNamespace(
        bus=bus,
        secret=SECRET,
        internal=INTERNAL,
        identity=httpx.AsyncClient(transport=id_t, base_url="http://identity"),
        tickets=httpx.AsyncClient(transport=tk_t, base_url="http://tickets"),
        notifications=httpx.AsyncClient(transport=nt_t, base_url="http://notifications"),
        gateway=httpx.AsyncClient(transport=gw_t, base_url="http://gateway"),
    )
    # Extra gateway clients (fresh cookie jar each) for multi-tenant UI tests.
    extra_gateway_clients: list[httpx.AsyncClient] = []

    def new_gateway_client() -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=gw_t, base_url="http://gateway")
        extra_gateway_clients.append(client)
        return client

    clients.new_gateway_client = new_gateway_client

    def token(*, user_id: str, tenant_id: str, role: str = "agent", email: str = "u@t.io") -> str:
        return create_access_token(
            user_id=user_id,
            tenant_id=tenant_id,
            role=role,
            email=email,
            secret_key=SECRET,
            expires_minutes=60,
        )

    async def signup(
        org_name: str, email: str, password: str = "password123", name: str = ""
    ) -> dict:
        resp = await clients.identity.post(
            "/auth/signup",
            json={"org_name": org_name, "email": email, "password": password, "name": name},
            headers=internal_headers(),
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    clients.token = token
    clients.signup = signup

    try:
        yield clients
    finally:
        for c in (clients.identity, clients.tickets, clients.notifications, clients.gateway):
            await c.aclose()
        for c in extra_gateway_clients:
            await c.aclose()
        await gateway.state.http_client.aclose()
        for app in (identity, tickets, notifications):
            await app.state.engine.dispose()
