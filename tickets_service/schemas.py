"""Pydantic v2 request/response schemas for the tickets-service."""

from __future__ import annotations

import datetime as dt
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Status = Literal["open", "pending", "resolved", "closed"]
Priority = Literal["low", "normal", "high", "urgent"]

_MAX_TAGS = 20
_MAX_TAG_LEN = 40


def _normalise_tags(tags: list[str] | None) -> list[str]:
    """Trim, de-duplicate (case-insensitively) and bound a list of tags."""
    if not tags:
        return []
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in tags:
        tag = raw.strip()[:_MAX_TAG_LEN]
        key = tag.lower()
        if tag and key not in seen:
            seen.add(key)
            cleaned.append(tag)
    return cleaned[:_MAX_TAGS]


class TicketCreate(BaseModel):
    subject: str = Field(min_length=3, max_length=300)
    description: str = Field(default="", max_length=10_000)
    priority: Priority = "normal"
    category: Optional[str] = Field(default=None, max_length=64)
    tags: list[str] = Field(default_factory=list)
    requester_email: Optional[EmailStr] = None
    assignee_user_id: Optional[str] = Field(default=None, max_length=32)

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: list[str]) -> list[str]:
        return _normalise_tags(v)


class TicketUpdate(BaseModel):
    # All optional: only provided fields are applied (PATCH semantics).
    subject: Optional[str] = Field(default=None, min_length=3, max_length=300)
    description: Optional[str] = Field(default=None, max_length=10_000)
    status: Optional[Status] = None
    priority: Optional[Priority] = None
    category: Optional[str] = Field(default=None, max_length=64)
    tags: Optional[list[str]] = None
    assignee_user_id: Optional[str] = Field(default=None, max_length=32)

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        return None if v is None else _normalise_tags(v)


class TicketAssign(BaseModel):
    # ``None`` clears the assignment (unassign); a value assigns to that user.
    assignee_user_id: Optional[str] = Field(default=None, max_length=32)


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    subject: str
    description: str
    status: Status
    priority: Priority
    category: Optional[str]
    tags: list[str]
    requester_email: Optional[EmailStr]
    assignee_user_id: Optional[str]
    created_by: str
    created_at: dt.datetime
    updated_at: dt.datetime


class TicketStats(BaseModel):
    """Per-tenant aggregate counts for the dashboard."""

    total: int
    by_status: dict[str, int]
    by_priority: dict[str, int]


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)
    # Optional parent for a threaded reply; must belong to the same ticket.
    parent_id: Optional[str] = Field(default=None, max_length=32)


class CommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    ticket_id: str
    parent_id: Optional[str]
    author_user_id: str
    body: str
    created_at: dt.datetime
