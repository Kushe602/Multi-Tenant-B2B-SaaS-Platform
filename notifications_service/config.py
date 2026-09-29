"""Settings for the notifications-service."""

from __future__ import annotations

from functools import lru_cache

from shared.config import BaseServiceSettings


class NotificationsSettings(BaseServiceSettings):
    service_name: str = "notifications-service"
    default_sqlite_url: str = "sqlite+aiosqlite:///./notifications.db"
    # When true (demo default), "emails" are only logged, never sent.
    email_demo_mode: bool = True


@lru_cache
def get_notifications_settings() -> NotificationsSettings:
    return NotificationsSettings()
