"""Settings for the identity-service."""

from __future__ import annotations

from functools import lru_cache

from shared.config import BaseServiceSettings


class IdentitySettings(BaseServiceSettings):
    service_name: str = "identity-service"
    default_sqlite_url: str = "sqlite+aiosqlite:///./identity.db"


@lru_cache
def get_identity_settings() -> IdentitySettings:
    return IdentitySettings()
