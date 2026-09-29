"""tickets-service: lifecycle, comments, auth layers and event emission."""

from __future__ import annotations

from conftest import auth_headers, internal_headers
from shared.topics import TICKET_ASSIGNED, TICKET_COMMENTED, TICKET_CREATED, TICKET_UPDATED


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


async def test_workflow_fields_category_tags_and_new_enum_values(system):
    token, _ = await _agent_token(system)
    created = await system.tickets.post(
        "/tickets",
        json={
            "subject": "Escalated billing",
            "priority": "urgent",
            "category": "billing",
            "tags": ["VIP", " vip ", "refund"],  # trimmed + case-insensitive dedupe
        },
        headers=auth_headers(token),
    )
    assert created.status_code == 201
    t = created.json()
    assert t["priority"] == "urgent"
    assert t["category"] == "billing"
    assert t["tags"] == ["VIP", "refund"]

    upd = await system.tickets.patch(
        f"/tickets/{t['id']}",
        json={"status": "resolved", "category": "payments", "tags": ["escalated"]},
        headers=auth_headers(token),
    )
    assert upd.status_code == 200
    body = upd.json()
    assert body["status"] == "resolved"
    assert body["category"] == "payments"
    assert body["tags"] == ["escalated"]


async def test_filter_and_search(system):
    token, user = await _agent_token(system)
    high = (
        await system.tickets.post(
            "/tickets",
            json={"subject": "Login fails", "description": "cannot sign in", "priority": "high"},
            headers=auth_headers(token),
        )
    ).json()
    await system.tickets.post(
        "/tickets",
        json={"subject": "Billing question", "priority": "low"},
        headers=auth_headers(token),
    )
    await system.tickets.post(
        "/tickets",
        json={"subject": "Login slow", "description": "dashboard lag", "priority": "normal"},
        headers=auth_headers(token),
    )
    # Assign one so the assignee filter has something to match.
    await system.tickets.post(
        f"/tickets/{high['id']}/assign",
        json={"assignee_user_id": user["id"]},
        headers=auth_headers(token),
    )

    by_priority = await system.tickets.get(
        "/tickets", params={"priority_filter": "high"}, headers=auth_headers(token)
    )
    assert [t["subject"] for t in by_priority.json()] == ["Login fails"]

    by_assignee = await system.tickets.get(
        "/tickets", params={"assignee": user["id"]}, headers=auth_headers(token)
    )
    assert [t["subject"] for t in by_assignee.json()] == ["Login fails"]

    search = await system.tickets.get(
        "/tickets", params={"q": "login"}, headers=auth_headers(token)
    )
    assert {t["subject"] for t in search.json()} == {"Login fails", "Login slow"}

    # Search also matches the description text.
    desc = await system.tickets.get(
        "/tickets", params={"q": "lag"}, headers=auth_headers(token)
    )
    assert [t["subject"] for t in desc.json()] == ["Login slow"]


async def test_stats_counts_are_zero_filled_and_tenant_scoped(system):
    token, _ = await _agent_token(system)
    for priority in ("low", "normal", "high"):
        await system.tickets.post(
            "/tickets",
            json={"subject": f"S {priority}", "priority": priority},
            headers=auth_headers(token),
        )
    first = (await system.tickets.get("/tickets", headers=auth_headers(token))).json()[0]
    await system.tickets.patch(
        f"/tickets/{first['id']}", json={"status": "resolved"}, headers=auth_headers(token)
    )

    stats = await system.tickets.get("/tickets/stats", headers=auth_headers(token))
    assert stats.status_code == 200
    data = stats.json()
    assert data["total"] == 3
    # Every enum value present (zero-filled), not just the ones that occurred.
    assert set(data["by_status"]) == {"open", "pending", "resolved", "closed"}
    assert set(data["by_priority"]) == {"low", "normal", "high", "urgent"}
    assert data["by_status"]["open"] == 2
    assert data["by_status"]["resolved"] == 1
    assert data["by_status"]["closed"] == 0
    assert data["by_priority"]["high"] == 1
    assert data["by_priority"]["urgent"] == 0


async def test_threaded_comment_replies(system):
    token, _ = await _agent_token(system)
    tid = (
        await system.tickets.post(
            "/tickets", json={"subject": "Threaded"}, headers=auth_headers(token)
        )
    ).json()["id"]

    root = await system.tickets.post(
        f"/tickets/{tid}/comments", json={"body": "root"}, headers=auth_headers(token)
    )
    assert root.status_code == 201
    root_id = root.json()["id"]

    reply = await system.tickets.post(
        f"/tickets/{tid}/comments",
        json={"body": "a reply", "parent_id": root_id},
        headers=auth_headers(token),
    )
    assert reply.status_code == 201
    assert reply.json()["parent_id"] == root_id

    # A parent_id that isn't a comment on this ticket is rejected.
    bad = await system.tickets.post(
        f"/tickets/{tid}/comments",
        json={"body": "orphan", "parent_id": "does-not-exist"},
        headers=auth_headers(token),
    )
    assert bad.status_code == 404


async def test_assignment_emits_event_and_can_unassign(system):
    token, user = await _agent_token(system)
    tid = (
        await system.tickets.post(
            "/tickets", json={"subject": "Assign me"}, headers=auth_headers(token)
        )
    ).json()["id"]

    assigned = await system.tickets.post(
        f"/tickets/{tid}/assign",
        json={"assignee_user_id": user["id"]},
        headers=auth_headers(token),
    )
    assert assigned.status_code == 200
    assert assigned.json()["assignee_user_id"] == user["id"]

    assign_events = [p for t, p in system.bus.published if t == TICKET_ASSIGNED]
    assert assign_events
    assert assign_events[-1]["assignee_user_id"] == user["id"]

    cleared = await system.tickets.post(
        f"/tickets/{tid}/assign", json={"assignee_user_id": None}, headers=auth_headers(token)
    )
    assert cleared.status_code == 200
    assert cleared.json()["assignee_user_id"] is None
