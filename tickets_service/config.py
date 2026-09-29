"""Settings for the tickets-service."""

from __future__ import annotations

from functools import lru_cache

from shared.config import BaseServiceSettings


class TicketsSettings(BaseServiceSettings):
    service_name: str = "tickets-service"
    default_sqlite_url: str = "sqlite+aiosqlite:///./tickets.db"


@lru_cache
def get_tickets_settings() -> TicketsSettings:
    return TicketsSettings()
