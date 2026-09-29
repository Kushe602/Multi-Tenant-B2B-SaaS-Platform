"""End-to-end integration through the gateway (web UI + REST API + event flow)."""

from __future__ import annotations


async def test_gateway_health(system):
    resp = await system.gateway.get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == "gateway"


async def test_public_pages_render(system):
    home = await system.gateway.get("/")
    assert home.status_code == 200
    assert "TenantForge" in home.text
    assert (await system.gateway.get("/login")).status_code == 200
    assert (await system.gateway.get("/signup")).status_code == 200


async def test_unauthenticated_api_rejected(system):
    assert (await system.gateway.get("/api/tickets")).status_code == 401
    assert (await system.gateway.get("/api/me")).status_code == 401


async def test_api_signup_then_full_ticket_and_notification_flow(system):
    gw = system.gateway
    signup = await gw.post(
        "/api/auth/signup",
        json={"org_name": "Acme", "email": "owner@acme.io", "password": "password123"},
    )
    assert signup.status_code == 201
    # Session cookie issued by the gateway.
    assert gw.cookies.get("tf_session")

    # Create a ticket through the public API (auth via the cookie).
    created = await gw.post("/api/tickets", json={"subject": "Gateway ticket", "priority": "high"})
    assert created.status_code == 201
    ticket_id = created.json()["id"]

    listing = await gw.get("/api/tickets")
    assert any(t["id"] == ticket_id for t in listing.json())

    # The ticket.created event should have produced a notification (shared bus).
    notes = await gw.get("/api/notifications")
    assert notes.status_code == 200
    assert any(n.get("ticket_id") == ticket_id for n in notes.json())


async def test_tenant_isolation_through_the_gateway(system):
    org_a = system.new_gateway_client()
    org_b = system.new_gateway_client()

    await org_a.post(
        "/api/auth/signup",
        json={"org_name": "Acme", "email": "a@acme.io", "password": "password123"},
    )
    await org_b.post(
        "/api/auth/signup",
        json={"org_name": "Beta", "email": "b@beta.io", "password": "password123"},
    )

    a_ticket = (await org_a.post("/api/tickets", json={"subject": "A-only"})).json()

    # Org B cannot fetch org A's ticket via the public API.
    assert (await org_b.get(f"/api/tickets/{a_ticket['id']}")).status_code == 404
    # And B's list never contains it.
    b_list = await org_b.get("/api/tickets")
    assert all(t["id"] != a_ticket["id"] for t in b_list.json())


async def test_web_signup_sets_cookie_and_dashboard_renders(system):
    gw = system.new_gateway_client()
    resp = await gw.post(
        "/signup",
        data={
            "org_name": "WebCo",
            "email": "web@webco.io",
            "password": "password123",
            "name": "Web User",
        },
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/app"
    assert gw.cookies.get("tf_session")

    dash = await gw.get("/app")
    assert dash.status_code == 200
    assert "Support tickets" in dash.text

    # HTMX partial for the ticket list renders (empty state initially).
    partial = await gw.get("/app/tickets")
    assert partial.status_code == 200
    assert "No tickets yet" in partial.text


async def test_web_dashboard_redirects_when_anonymous(system):
    resp = await system.gateway.get("/app")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
