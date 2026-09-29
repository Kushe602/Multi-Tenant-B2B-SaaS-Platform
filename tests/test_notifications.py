"""notifications-service: event consumption and tenant-scoped read API."""

from __future__ import annotations

from conftest import auth_headers
from shared.topics import TICKET_ASSIGNED, TICKET_CREATED, TICKET_UPDATED


def _event(tenant_id: str, ticket_id: str, event: str = "created", subject: str = "Subj") -> dict:
    return {
        "event": event,
        "tenant_id": tenant_id,
        "ticket_id": ticket_id,
        "subject": subject,
        "status": "open",
        "priority": "normal",
        "assignee_user_id": None,
        "actor_user_id": "actor",
    }


async def test_event_is_consumed_into_a_notification(system):
    await system.bus.publish(TICKET_CREATED, _event("tenantA", "ticket-1", subject="Login broken"))

    token = system.token(user_id="u1", tenant_id="tenantA", role="owner")
    resp = await system.notifications.get("/notifications", headers=auth_headers(token))
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["type"] == "ticket.created"
    assert "Login broken" in items[0]["title"]
    assert items[0]["ticket_id"] == "ticket-1"


async def test_notifications_are_tenant_scoped(system):
    await system.bus.publish(TICKET_CREATED, _event("tenantA", "t-a"))
    await system.bus.publish(TICKET_UPDATED, _event("tenantB", "t-b", event="updated"))

    a_token = system.token(user_id="ua", tenant_id="tenantA")
    b_token = system.token(user_id="ub", tenant_id="tenantB")

    a_resp = await system.notifications.get("/notifications", headers=auth_headers(a_token))
    b_resp = await system.notifications.get("/notifications", headers=auth_headers(b_token))
    a_items = a_resp.json()
    b_items = b_resp.json()

    assert {n["ticket_id"] for n in a_items} == {"t-a"}
    assert {n["ticket_id"] for n in b_items} == {"t-b"}


async def test_unread_count_and_mark_read(system):
    await system.bus.publish(TICKET_CREATED, _event("tenantA", "t1"))
    await system.bus.publish(TICKET_CREATED, _event("tenantA", "t2"))
    token = system.token(user_id="u1", tenant_id="tenantA")

    count = await system.notifications.get(
        "/notifications/unread_count", headers=auth_headers(token)
    )
    assert count.json()["unread"] == 2

    listing = await system.notifications.get("/notifications", headers=auth_headers(token))
    first = listing.json()[0]
    marked = await system.notifications.post(
        f"/notifications/{first['id']}/read", headers=auth_headers(token)
    )
    assert marked.status_code == 200
    assert marked.json()["read"] is True

    count2 = await system.notifications.get(
        "/notifications/unread_count", headers=auth_headers(token)
    )
    assert count2.json()["unread"] == 1


async def test_cannot_mark_read_across_tenants(system):
    await system.bus.publish(TICKET_CREATED, _event("tenantA", "t1"))
    a_token = system.token(user_id="ua", tenant_id="tenantA")
    b_token = system.token(user_id="ub", tenant_id="tenantB")

    a_listing = await system.notifications.get("/notifications", headers=auth_headers(a_token))
    note = a_listing.json()[0]
    # Tenant B tries to mark tenant A's notification read -> 404.
    resp = await system.notifications.post(
        f"/notifications/{note['id']}/read", headers=auth_headers(b_token)
    )
    assert resp.status_code == 404


async def test_assignment_event_creates_targeted_notification(system):
    payload = _event("tenantA", "t-assign", event="assigned", subject="Please handle")
    payload["assignee_user_id"] = "assignee-1"
    await system.bus.publish(TICKET_ASSIGNED, payload)

    token = system.token(user_id="assignee-1", tenant_id="tenantA")
    resp = await system.notifications.get("/notifications", headers=auth_headers(token))
    assert resp.status_code == 200
    items = resp.json()
    assigned = [n for n in items if n["type"] == "ticket.assigned"]
    assert len(assigned) == 1
    # The notification is targeted at the assignee (used for per-user delivery).
    assert assigned[0]["user_id"] == "assignee-1"
    assert assigned[0]["ticket_id"] == "t-assign"
