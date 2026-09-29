"""Pydantic v2 schemas for the notifications-service."""

from __future__ import annotations

import datetime as dt
from typing import Optional

from pydantic import BaseModel, ConfigDict


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    user_id: Optional[str]
    type: str
    title: str
    body: str
    ticket_id: Optional[str]
    read: bool
    created_at: dt.datetime
