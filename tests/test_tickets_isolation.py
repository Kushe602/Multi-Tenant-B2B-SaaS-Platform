"""TENANT ISOLATION -- the headline security property.

A user of tenant A must never be able to read or mutate tenant B's tickets.
``tenant_id`` is derived from the signed JWT, so even a valid, authenticated
user of another tenant is blocked (404, which also avoids leaking existence).
"""

from __future__ import annotations

from conftest import auth_headers


async def _two_tenants(system):
    a = await system.signup("Acme", "owner@acme.io")
    b = await system.signup("Beta", "owner@beta.io")
    a_token = a["access_token"]
    b_token = b["access_token"]
    a_ticket = (
        await system.tickets.post(
            "/tickets",
            json={"subject": "Acme private issue", "description": "secret"},
            headers=auth_headers(a_token),
        )
    ).json()
    return a_token, b_token, a_ticket


async def test_tenant_cannot_read_another_tenants_ticket(system):
    a_token, b_token, a_ticket = await _two_tenants(system)
    # Owner of tenant A can read it.
    assert (
        await system.tickets.get(f"/tickets/{a_ticket['id']}", headers=auth_headers(a_token))
    ).status_code == 200
    # Owner of tenant B cannot -- 404, not 200.
    resp = await system.tickets.get(f"/tickets/{a_ticket['id']}", headers=auth_headers(b_token))
    assert resp.status_code == 404


async def test_tenant_cannot_update_another_tenants_ticket(system):
    _a_token, b_token, a_ticket = await _two_tenants(system)
    resp = await system.tickets.patch(
        f"/tickets/{a_ticket['id']}", json={"status": "closed"}, headers=auth_headers(b_token)
    )
    assert resp.status_code == 404


async def test_tenant_cannot_comment_on_another_tenants_ticket(system):
    _a_token, b_token, a_ticket = await _two_tenants(system)
    resp = await system.tickets.post(
        f"/tickets/{a_ticket['id']}/comments",
        json={"body": "leaking?"},
        headers=auth_headers(b_token),
    )
    assert resp.status_code == 404


async def test_tenant_cannot_read_another_tenants_comments(system):
    a_token, b_token, a_ticket = await _two_tenants(system)
    await system.tickets.post(
        f"/tickets/{a_ticket['id']}/comments",
        json={"body": "internal note"},
        headers=auth_headers(a_token),
    )
    resp = await system.tickets.get(
        f"/tickets/{a_ticket['id']}/comments", headers=auth_headers(b_token)
    )
    assert resp.status_code == 404


async def test_list_never_returns_another_tenants_tickets(system):
    _a_token, b_token, a_ticket = await _two_tenants(system)
    # Tenant B lists tickets -> must not include tenant A's ticket.
    b_list = await system.tickets.get("/tickets", headers=auth_headers(b_token))
    assert b_list.status_code == 200
    assert all(t["id"] != a_ticket["id"] for t in b_list.json())
    assert b_list.json() == []


async def test_tenant_scope_comes_from_token_not_a_forged_id(system):
    _a_token, _b_token, a_ticket = await _two_tenants(system)
    # A perfectly valid JWT, but for a made-up tenant: still cannot reach A's data.
    forged = system.token(user_id="ghost", tenant_id="tenant-that-does-not-exist", role="owner")
    assert (
        await system.tickets.get(f"/tickets/{a_ticket['id']}", headers=auth_headers(forged))
    ).status_code == 404
    assert (await system.tickets.get("/tickets", headers=auth_headers(forged))).json() == []
