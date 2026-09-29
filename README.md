# TenantForge

**A multi-tenant B2B SaaS helpdesk, built as real microservices with a hard tenant-isolation guarantee.**

[![CI](https://github.com/Kushe602/Multi-Tenant-B2B-SaaS-Platform/actions/workflows/ci.yml/badge.svg)](https://github.com/Kushe602/Multi-Tenant-B2B-SaaS-Platform/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%E2%80%93%203.14-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

TenantForge is a customer-support ticketing platform for B2B SaaS. Businesses
sign up as **tenants** (organizations); each tenant has **users** with roles
(`owner`, `agent`); agents manage **support tickets** that belong strictly to
their own tenant. It is built as four independently deployable FastAPI services
communicating over HTTP and a Redis event bus.

The headline property is **tenant isolation**: a user of tenant A can never read
or mutate tenant B's data. Tenant scope is derived from a signed JWT (never from
client input) and enforced on *every* query. This is proven by a dedicated test
suite (see [Tenant isolation](#tenant-isolation)).

> **Runs with zero infrastructure.** With no `DATABASE_URL`/`REDIS_URL` set, the
> stack falls back to SQLite + an in-process event bus, so `pytest` and even
> `uvicorn` run with **no Docker, PostgreSQL, or Redis**. Point the same code at
> PostgreSQL + Redis for production.

---

## What this project demonstrates

- **Genuine microservices** — four services, each with its own app factory,
  database schema, and Dockerfile; a single public edge (gateway).
- **Multi-tenancy done safely** — shared-database / shared-schema model with a
  single enforced choke point, plus a documented migration path to
  schema-per-tenant and database-per-tenant.
- **Layered auth** — bcrypt password hashing, HS256 JWTs, RBAC, and a
  service-to-service internal-secret handshake so only the gateway can reach the
  internal services.
- **Event-driven design** — `ticket.*` domain events on a Redis pub/sub bus,
  consumed asynchronously into in-app notifications.
- **Testability** — a dual-mode infrastructure layer makes the entire
  cross-service flow (web UI + REST + events) testable in-process, deterministically.
- **Production shape** — pinned deps, `ruff` lint, per-service Dockerfiles,
  `docker compose` stack with healthchecks, and GitHub Actions CI.

---

## Architecture

```
                              ┌─────────────────────────────┐
        Browser / API client  │  Web UI (HTMX + Jinja2)      │
                 │            │  Public REST API (/api/*)    │
                 ▼            └─────────────────────────────┘
        ┌───────────────────────────────────────────────────────────┐
        │                        GATEWAY  :8000                       │
        │  • ONLY publicly-exposed service (stateless, no DB)         │
        │  • Validates the JWT session cookie                         │
        │  • Renders HTMX web UI + proxies the REST API               │
        │  • Adds X-Internal-Secret + Bearer <jwt> to downstream calls│
        └───────────────┬──────────────┬───────────────┬─────────────┘
                        │ httpx        │ httpx         │ httpx
             ┌──────────▼───────┐ ┌────▼───────────┐ ┌─▼───────────────────┐
             │ IDENTITY  :8001  │ │ TICKETS  :8002 │ │ NOTIFICATIONS :8003 │
             │ tenants, users,  │ │ tickets +      │ │ consumes ticket.*   │
             │ auth, RBAC,      │ │ comments;      │ │ events -> in-app    │
             │ invites          │ │ tenant-scoped  │ │ notifications       │
             └────────┬─────────┘ └───────┬────────┘ └──────────▲──────────┘
                      │                   │                     │
                      │                   │  publish ticket.*   │  subscribe
                      │                   └─────────┐           │
                      ▼                   ▼         ▼           │
             ┌──────────────────────────────────┐  ┌───────────┴───────────┐
             │   PostgreSQL (database-per-svc)   │  │   Redis event bus      │
             │   identity / tickets / notif.     │  │   (pub/sub: tf:*)      │
             └──────────────────────────────────┘  └───────────────────────┘

   Demo mode (no env): PostgreSQL -> per-service SQLite,  Redis -> in-process bus.
```

Only the **gateway** is reachable from outside. The three internal services
trust a call only when it carries the shared `X-Internal-Secret` header, and
they derive tenant/role from the **signed JWT** — never from client-supplied
fields.

| Service | Responsibility | Owns |
| --- | --- | --- |
| `gateway` | Public edge: web UI + REST API, session cookie, fan-out | *(stateless)* |
| `identity_service` | Tenants, users, auth (bcrypt + JWT), RBAC, invites | `tenants`, `users`, `invites` |
| `tickets_service` | Tickets + comments; emits `ticket.*` events | `tickets`, `comments` |
| `notifications_service` | Consumes events into in-app notifications | `notifications` |
| `shared/` | Reusable library (config, db, security, events, deps) | — |

---

## Multi-tenancy model

TenantForge uses the **shared-database, shared-schema** model: one database per
service, and **every domain row carries a `tenant_id`**. Every query is filtered
by the caller's `tenant_id`. This is the most common and cost-efficient SaaS
model — cheap per-tenant onboarding, one schema to migrate, easy cross-tenant
analytics — at the cost of relying on disciplined query scoping for isolation.

To keep that discipline enforceable, tenant scoping lives at a **single choke
point** rather than being sprinkled across handlers. In `tickets_service`:

```python
async def _owned_ticket(session, principal, ticket_id):
    ticket = await session.scalar(
        select(Ticket).where(
            Ticket.id == ticket_id,
            Ticket.tenant_id == principal.tenant_id,   # <- tenant scope, always
        )
    )
    if ticket is None:
        # 404 (not 403): never reveal that another tenant's row exists.
        raise HTTPException(status_code=404, detail="ticket not found")
    return ticket
```

Two details that matter:

- **`tenant_id` comes from the signed JWT, not the request.** A perfectly valid,
  authenticated user of another tenant — or a forged `tenant_id` in the body —
  still resolves to *their* scope. There is no request field that can widen it.
- **Cross-tenant access returns `404`, not `403`.** Returning "forbidden" would
  leak the existence of another tenant's records; "not found" does not.

### Isolation guarantee

> For any resource `R` owned by tenant `A`, a request authenticated as tenant
> `B` (`B != A`) can never read or mutate `R`, and `R` never appears in any list
> returned to `B`. The response is indistinguishable from `R` not existing.

This is enforced on reads, updates, comments, and list endpoints, and verified
by [`tests/test_tickets_isolation.py`](tests/test_tickets_isolation.py).

### Alternatives (and when to graduate to them)

| Model | Isolation | Cost / ops | When |
| --- | --- | --- | --- |
| **Shared DB, shared schema** *(used here)* | Logical (`tenant_id` filter) | Lowest; one migration | Most SaaS; many small/medium tenants |
| **Shared DB, schema-per-tenant** | Stronger (Postgres `search_path`) | Migrate N schemas; heavier catalog | Mid-market; per-tenant customization |
| **Database-per-tenant** | Strongest (physical) | Highest; N databases to operate | Enterprise, compliance, noisy-neighbor isolation |

Because scoping is centralized, graduating is incremental: swap
`_owned_ticket` / session acquisition to set a per-request `search_path`
(schema-per-tenant) or to select a per-tenant engine (database-per-tenant),
without touching handler logic. The services already use **database-per-service**
(identity / tickets / notifications each own a database), which is orthogonal to
tenant isolation and demonstrated in `docker-compose.yml`.

---

## Security

- **Two-layer downstream auth.** Every internal call must present the shared
  `X-Internal-Secret` header (proving it came from the gateway → `403` otherwise)
  *and* a `Bearer <jwt>`. Tenant and role are read from the **signed** token
  (`401` on a missing/invalid/expired token), never from client input. Secrets
  are compared with `hmac.compare_digest` to avoid timing leaks.
- **Passwords** are hashed with **bcrypt** (input truncated to 72 bytes); login
  is timing-safe against a placeholder hash so "user exists" cannot be probed.
- **JWTs** are HS256, signed with `SECRET_KEY`, carrying `sub`, `tenant_id`,
  `role`, `email`, `iat`, `exp`, `jti`.
- **Session cookie** is `HttpOnly`, `SameSite=Lax`, and `Secure`-configurable
  (`COOKIE_SECURE=true` behind HTTPS).
- **No raw SQL** — all access goes through SQLAlchemy 2.0 (parameterized);
  inputs are validated by pydantic v2 models (invalid enums → `422`).
- **RBAC** — sensitive actions (e.g. creating invites) require the `owner` role.
- **No committed secrets.** Defaults are clearly-labelled dev placeholders;
  production must override `SECRET_KEY` and `INTERNAL_SECRET` via the environment.

> **Note:** the dev placeholder secrets in `.env.example` / compose are *not*
> real secrets and are safe to run locally only. Always override them in any
> shared or production environment.

---

## Scalability

- **Stateless gateway.** No server-side session store (the JWT *is* the session),
  so the public edge scales horizontally behind a load balancer with no stickiness.
- **Independent services.** Each service scales on its own axis — the write-heavy
  `tickets_service` and the event-driven `notifications_service` scale
  separately from `identity_service`.
- **Async end-to-end.** FastAPI + async SQLAlchemy + async httpx means requests
  are I/O-bound and non-blocking; `asyncpg` provides connection pooling.
- **Event-driven decoupling.** Ticket writes publish events and return
  immediately; notification work happens asynchronously off the Redis bus, so a
  slow consumer never slows the write path. New consumers (email, webhooks,
  search indexing) subscribe without touching producers.
- **Database-per-service** keeps schemas and scaling decisions isolated, and
  leaves room to shard or graduate a single tenant to its own database.

---

## Tech stack

Python 3.11–3.14 · FastAPI · Starlette · async SQLAlchemy 2.0 · PostgreSQL
(`asyncpg`) / SQLite (`aiosqlite`) · Redis pub/sub · pydantic v2 +
pydantic-settings · PyJWT · bcrypt · httpx · Jinja2 + HTMX + Tailwind (CDN) ·
pytest + pytest-asyncio · ruff · Docker + docker compose · GitHub Actions.

---

## Getting started

### Option A — zero-config demo (no Docker, no PostgreSQL, no Redis)

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements-dev.txt

# Lint + run the full test suite (SQLite + in-process event bus).
ruff check .
pytest
```

To click around the UI without any infrastructure, run the services in demo
mode in four terminals (each falls back to its own SQLite file + in-process bus):

```bash
uvicorn identity_service.main:app      --port 8001
uvicorn tickets_service.main:app       --port 8002
uvicorn notifications_service.main:app --port 8003
uvicorn gateway.main:app               --port 8000
```

Then open <http://localhost:8000>. Note: in this split demo the in-process event
bus is per-process, so cross-service notifications require the Redis bus — use
Option B for the full event flow. (The automated tests exercise the full event
flow in one process via a shared in-memory bus.)

### Option B — full stack with docker compose (PostgreSQL + Redis)

```bash
docker compose up --build
```

- Web UI + API: <http://localhost:8000>
- Only the gateway is published; the internal services and datastores stay on
  the compose network.
- Each service gets its own PostgreSQL database and shares the Redis event bus,
  so `ticket.*` events flow end-to-end into notifications.

### Try the API

```bash
# Sign up an organization (sets an HttpOnly session cookie).
curl -c jar.txt -X POST http://localhost:8000/api/auth/signup \
  -H 'Content-Type: application/json' \
  -d '{"org_name":"Acme","email":"owner@acme.io","password":"password123"}'

# Create a ticket (auth via the cookie).
curl -b jar.txt -X POST http://localhost:8000/api/tickets \
  -H 'Content-Type: application/json' \
  -d '{"subject":"Cannot log in","priority":"high"}'

# List your tenant's tickets, and see the notification the event produced.
curl -b jar.txt http://localhost:8000/api/tickets
curl -b jar.txt http://localhost:8000/api/notifications
```

---

## API overview

All `/api/*` routes require authentication (session cookie or `Bearer` token).
Interactive docs are served per service at `/docs` (e.g. the gateway proxies the
public surface; each service exposes its own OpenAPI in dev).

| Method & path | Purpose |
| --- | --- |
| `POST /api/auth/signup` | Create org + owner, set session cookie |
| `POST /api/auth/login` | Authenticate, set session cookie |
| `POST /api/auth/logout` | Clear session cookie |
| `GET  /api/me` | Current principal |
| `GET  /api/users` | Users in the caller's tenant |
| `POST /api/invites` | Invite a teammate *(owner only)* |
| `GET  /api/tickets` | List tenant tickets (`?status_filter=open\|pending\|closed`) |
| `POST /api/tickets` | Create a ticket → emits `ticket.created` |
| `GET  /api/tickets/{id}` | Fetch one ticket (`404` if not in tenant) |
| `PATCH /api/tickets/{id}` | Update status/assignee → emits `ticket.updated` |
| `GET/POST /api/tickets/{id}/comments` | List/add comments → emits `ticket.commented` |
| `GET  /api/notifications` | Tenant-scoped in-app notifications |
| `POST /api/notifications/{id}/read` | Mark one read |

The gateway also serves a server-rendered web UI (HTMX + Jinja2 + Tailwind) at
`/`, `/login`, `/signup`, and the authenticated dashboard at `/app`.

---

## Testing

```bash
pytest            # 29 tests, all in zero-config demo mode
ruff check .      # lint
```

A single `system` fixture ([`tests/conftest.py`](tests/conftest.py)) builds all
four apps in one process, wires them with a **shared in-memory event bus** and
per-service in-memory SQLite, and routes the gateway's httpx client to the
in-process services — so web UI, REST, RBAC, and the full event flow are all
tested deterministically without Docker.

| File | Covers |
| --- | --- |
| `tests/test_identity.py` | Signup, login, RBAC, invites, JWT claims |
| `tests/test_tickets.py` | Ticket lifecycle, comments, auth layers, event emission |
| `tests/test_tickets_isolation.py` | **Tenant isolation (headline security property)** |
| `tests/test_notifications.py` | Event consumption, tenant-scoped reads, unread/mark-read |
| `tests/test_gateway_integration.py` | End-to-end via the gateway (UI + API + events) |

### Tenant isolation

`tests/test_tickets_isolation.py`:

- `test_tenant_cannot_read_another_tenants_ticket`
- `test_tenant_cannot_update_another_tenants_ticket`
- `test_tenant_cannot_comment_on_another_tenants_ticket`
- `test_tenant_cannot_read_another_tenants_comments`
- `test_list_never_returns_another_tenants_tickets`
- `test_tenant_scope_comes_from_token_not_a_forged_id`

`tests/test_gateway_integration.py`:

- `test_tenant_isolation_through_the_gateway`

---

## Project structure

```
tenantforge/
├── gateway/                 # Public edge: web UI + REST API (stateless)
│   ├── main.py  clients.py  deps.py  config.py
│   ├── routes_api.py  routes_web.py
│   ├── templates/           # Jinja2 + HTMX (+ partials/)
│   └── Dockerfile
├── identity_service/        # Tenants, users, auth, RBAC, invites
├── tickets_service/         # Tickets + comments; emits ticket.* events
├── notifications_service/   # Event consumer + tenant-scoped read API
│   └── (each: main.py models.py routes.py schemas.py config.py Dockerfile)
├── shared/                  # config, database, security, events, deps, runtime, topics
├── tests/                   # conftest.py + 5 test modules (in-process, no Docker)
├── deploy/postgres/         # init-databases.sql (database-per-service)
├── docker-compose.yml       # gateway + 3 services + postgres + redis (healthchecks)
├── .github/workflows/ci.yml # ruff + pytest (no secrets)
├── requirements.txt  requirements-dev.txt
├── pyproject.toml           # ruff + pytest config
└── .env.example  .gitignore  .dockerignore  LICENSE
```

Each service follows the same **app-factory** pattern (`create_app(settings)`);
runtime state (engine, sessionmaker, event bus) lives on `app.state` and is
initialized in the FastAPI lifespan via `shared/runtime.py`.

---

## Configuration

All settings load from environment variables (or a `.env` file) via
`pydantic-settings`. See [`.env.example`](.env.example) for the full list.

| Variable | Default | Notes |
| --- | --- | --- |
| `SECRET_KEY` | dev placeholder | HS256 JWT signing key — **override in prod** |
| `INTERNAL_SECRET` | dev placeholder | Service-to-service handshake — **override in prod** |
| `DATABASE_URL` | *(unset)* | `postgresql+asyncpg://…`; unset → SQLite demo mode |
| `REDIS_URL` | *(unset)* | `redis://…`; unset → in-process event bus |
| `JWT_EXPIRE_MINUTES` | `1440` | Session length |
| `COOKIE_SECURE` | `false` | Set `true` behind HTTPS |
| `COOKIE_SAMESITE` | `lax` | Session cookie policy |
| `EMAIL_DEMO_MODE` | `true` | When true, "emails" are only logged |
| `IDENTITY_URL` / `TICKETS_URL` / `NOTIFICATIONS_URL` | localhost:8001-8003 | Gateway → service URLs |

---

## Roadmap / notes

- **Migrations.** Demo/dev create tables at startup (`create_all`). A production
  deployment should adopt Alembic migrations per service instead.
- **Event durability.** Redis pub/sub is fire-and-forget; a production build
  would use Streams (or an outbox pattern) for at-least-once delivery.
- **Refresh tokens / rotation.** Only short-lived access tokens exist today.
- **Real email/webhook delivery** for notifications (currently faked in demo mode).

---

## License

[MIT](LICENSE) © 2026 Kushe Jawadwala





