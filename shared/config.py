"""Base settings shared by every service.

Settings are loaded with ``pydantic-settings`` from environment variables (and
an optional ``.env`` file). The two knobs that drive *zero-config demo mode*:

* ``database_url`` unset  -> the service falls back to a local SQLite file
  (``aiosqlite``) so it runs with no PostgreSQL.
* ``redis_url`` unset     -> the service uses an in-process in-memory event bus
  so it runs with no Redis / broker.

That is what lets ``pytest`` and ``uvicorn`` run with no external services.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

# Dev-only placeholders. These are intentionally NOT real secrets; production
# deployments MUST override SECRET_KEY / INTERNAL_SECRET via the environment.
# Kept >= 32 bytes so HS256 signing does not emit a weak-key warning.
_DEV_SECRET_KEY = "dev-only-insecure-secret-key-change-me-0123456789"
_DEV_INTERNAL_SECRET = "dev-only-internal-service-secret-change-me-01234"


class BaseServiceSettings(BaseSettings):
    """Configuration common to all TenantForge services."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Identity of this service (overridden by each subclass).
    service_name: str = "service"

    # Auth / crypto.
    secret_key: str = _DEV_SECRET_KEY
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24  # 24h sessions
    internal_secret: str = _DEV_INTERNAL_SECRET

    # Infrastructure (unset => demo fallbacks).
    database_url: str | None = None
    redis_url: str | None = None

    # Cookie hardening for the gateway.
    cookie_secure: bool = False
    cookie_samesite: str = "lax"

    environment: str = "development"

    # Overridden per service so demo mode picks a distinct SQLite file.
    default_sqlite_url: str = "sqlite+aiosqlite:///./tenantforge.db"

    @property
    def resolved_database_url(self) -> str:
        """The database URL actually used (real one, or the SQLite fallback)."""
        return self.database_url or self.default_sqlite_url

    @property
    def is_demo_database(self) -> bool:
        return self.database_url is None

    @property
    def is_demo_bus(self) -> bool:
        return self.redis_url is None
