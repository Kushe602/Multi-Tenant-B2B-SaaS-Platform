"""Settings for the gateway (the only publicly-exposed service)."""

from __future__ import annotations

from functools import lru_cache

from shared.config import BaseServiceSettings


class GatewaySettings(BaseServiceSettings):
    service_name: str = "gateway"

    # Downstream service base URLs. Defaults target local dev; docker-compose
    # overrides these with the service DNS names (http://identity:8000 etc.).
    identity_url: str = "http://localhost:8001"
    tickets_url: str = "http://localhost:8002"
    notifications_url: str = "http://localhost:8003"

    request_timeout: float = 10.0
    cookie_name: str = "tf_session"


@lru_cache
def get_gateway_settings() -> GatewaySettings:
    return GatewaySettings()
