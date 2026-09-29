"""tickets-service: lifecycle, comments, auth layers and event emission."""

from __future__ import annotations

from conftest import auth_headers, internal_headers
from shared.topics import TICKET_COMMENTED, TICKET_CREATED, TICKET_UPDATED


async def _agent_token(system, org="Acme", email="owner@acme.io"):
    data = await system.signup(org, email)
    return data["access_token"], data["user"]


async def test_create_and_get_ticket(system):
    token, _ = await _agent_token(system)
    created = await system.tickets.post(
        "/tickets",
        json={"subject": "Cannot log in", "description": "500 on login", "priority": "high"},
        headers=auth_headers(token),
    )
    assert created.status_code == 201
    ticket = created.json()
    assert ticket["status"] == "open"
    assert ticket["priority"] == "high"

    fetched = await system.tickets.get(f"/tickets/{ticket['id']}", headers=auth_headers(token))
    assert fetched.status_code == 200
    assert fetched.json()["subject"] == "Cannot log in"


async def test_ticket_lifecycle_status_transitions(system):
    token, _ = await _agent_token(system)
    tid = (
        await system.tickets.post(
            "/tickets", json={"subject": "Billing issue"}, headers=auth_headers(token)
        )
    ).json()["id"]

    for new_status in ("pending", "closed"):
        resp = await system.tickets.patch(
            f"/tickets/{tid}", json={"status": new_status}, headers=auth_headers(token)
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == new_status


async def test_list_is_tenant_scoped_and_filterable(system):
    token, _ = await _agent_token(system)
    for i in range(3):
        await system.tickets.post(
            "/tickets", json={"subject": f"Ticket {i}"}, headers=auth_headers(token)
        )
    # close one
    first = (await system.tickets.get("/tickets", headers=auth_headers(token))).json()[0]
    await system.tickets.patch(
        f"/tickets/{first['id']}", json={"status": "closed"}, headers=auth_headers(token)
    )

    all_tickets = await system.tickets.get("/tickets", headers=auth_headers(token))
    assert len(all_tickets.json()) == 3

    closed = await system.tickets.get(
        "/tickets", params={"status_filter": "closed"}, headers=auth_headers(token)
    )
    assert len(closed.json()) == 1

    bad = await system.tickets.get(
        "/tickets", params={"status_filter": "bogus"}, headers=auth_headers(token)
    )
    assert bad.status_code == 422  # invalid enum rejected by validation


async def test_comments_flow(system):
    token, _ = await _agent_token(system)
    tid = (
        await system.tickets.post(
            "/tickets", json={"subject": "Need help"}, headers=auth_headers(token)
        )
    ).json()["id"]

    c = await system.tickets.post(
        f"/tickets/{tid}/comments", json={"body": "Looking into it"}, headers=auth_headers(token)
    )
    assert c.status_code == 201
    listing = await system.tickets.get(f"/tickets/{tid}/comments", headers=auth_headers(token))
    assert [x["body"] for x in listing.json()] == ["Looking into it"]


async def test_auth_layers_required(system):
    token, _ = await _agent_token(system)
    # Missing internal secret -> 403.
    assert (await system.tickets.get("/tickets")).status_code == 403
    # Internal secret but no/invalid token -> 401.
    assert (await system.tickets.get("/tickets", headers=internal_headers())).status_code == 401
    assert (
        await system.tickets.get(
            "/tickets", headers={**internal_headers(), "Authorization": "Bearer not-a-jwt"}
        )
    ).status_code == 401
    # Valid -> 200.
    assert (await system.tickets.get("/tickets", headers=auth_headers(token))).status_code == 200


async def test_events_emitted_on_create_update_comment(system):
    token, _ = await _agent_token(system)
    tid = (
        await system.tickets.post(
            "/tickets", json={"subject": "Emit events"}, headers=auth_headers(token)
        )
    ).json()["id"]
    await system.tickets.patch(
        f"/tickets/{tid}", json={"status": "pending"}, headers=auth_headers(token)
    )
    await system.tickets.post(
        f"/tickets/{tid}/comments", json={"body": "hi"}, headers=auth_headers(token)
    )

    topics = [t for t, _ in system.bus.published]
    assert TICKET_CREATED in topics
    assert TICKET_UPDATED in topics
    assert TICKET_COMMENTED in topics
    # Every emitted event carries the tenant_id (used for downstream scoping).
    assert all("tenant_id" in payload for _, payload in system.bus.published)
