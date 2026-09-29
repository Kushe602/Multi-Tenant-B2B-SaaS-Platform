"""identity-service: auth, RBAC and tenant-scoped invites."""

from __future__ import annotations

from conftest import auth_headers, internal_headers


async def test_signup_creates_tenant_and_owner(system):
    data = await system.signup("Acme Support", "owner@acme.io", name="Ada")
    assert data["user"]["role"] == "owner"
    assert data["user"]["email"] == "owner@acme.io"
    assert data["tenant"]["name"] == "Acme Support"
    assert data["tenant"]["slug"] == "acme-support"
    assert data["access_token"]


async def test_duplicate_email_rejected(system):
    await system.signup("Acme", "dup@acme.io")
    resp = await system.identity.post(
        "/auth/signup",
        json={"org_name": "Other", "email": "dup@acme.io", "password": "password123"},
        headers=internal_headers(),
    )
    assert resp.status_code == 409


async def test_login_success_and_wrong_password(system):
    await system.signup("Acme", "owner@acme.io", password="password123")

    ok = await system.identity.post(
        "/auth/login",
        json={"email": "owner@acme.io", "password": "password123"},
        headers=internal_headers(),
    )
    assert ok.status_code == 200
    assert ok.json()["access_token"]

    bad = await system.identity.post(
        "/auth/login",
        json={"email": "owner@acme.io", "password": "wrong-password"},
        headers=internal_headers(),
    )
    assert bad.status_code == 401


async def test_me_requires_internal_secret_and_token(system):
    data = await system.signup("Acme", "owner@acme.io")
    token = data["access_token"]

    # No headers at all -> internal-secret layer rejects first (403).
    assert (await system.identity.get("/me")).status_code == 403
    # Internal secret but no bearer -> 401.
    assert (await system.identity.get("/me", headers=internal_headers())).status_code == 401
    # Both -> 200.
    good = await system.identity.get("/me", headers=auth_headers(token))
    assert good.status_code == 200
    assert good.json()["email"] == "owner@acme.io"


async def test_owner_can_invite_agent_cannot(system):
    owner = await system.signup("Acme", "owner@acme.io")
    owner_token = owner["access_token"]

    # Owner creates an invite for an agent.
    inv = await system.identity.post(
        "/invites",
        json={"email": "agent@acme.io", "role": "agent"},
        headers=auth_headers(owner_token),
    )
    assert inv.status_code == 201
    invite_token = inv.json()["token"]

    # Accept the invite -> new agent user in the SAME tenant.
    accepted = await system.identity.post(
        "/invites/accept",
        json={"token": invite_token, "name": "Agent", "password": "password123"},
        headers=internal_headers(),
    )
    assert accepted.status_code == 201
    agent = accepted.json()
    assert agent["user"]["role"] == "agent"
    assert agent["user"]["tenant_id"] == owner["user"]["tenant_id"]

    # An agent must NOT be able to create invites (RBAC).
    forbidden = await system.identity.post(
        "/invites",
        json={"email": "another@acme.io", "role": "agent"},
        headers=auth_headers(agent["access_token"]),
    )
    assert forbidden.status_code == 403


async def test_users_list_is_tenant_scoped(system):
    a = await system.signup("Acme", "owner@acme.io")
    b = await system.signup("Beta", "owner@beta.io")

    a_users = await system.identity.get("/users", headers=auth_headers(a["access_token"]))
    b_users = await system.identity.get("/users", headers=auth_headers(b["access_token"]))

    a_emails = {u["email"] for u in a_users.json()}
    b_emails = {u["email"] for u in b_users.json()}
    assert a_emails == {"owner@acme.io"}
    assert b_emails == {"owner@beta.io"}
    assert a_emails.isdisjoint(b_emails)
