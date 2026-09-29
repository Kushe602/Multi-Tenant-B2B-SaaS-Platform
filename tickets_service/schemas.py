"""Pydantic v2 request/response schemas for the tickets-service."""

from __future__ import annotations

import datetime as dt
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Status = Literal["open", "pending", "closed"]
Priority = Literal["low", "normal", "high"]


class TicketCreate(BaseModel):
    subject: str = Field(min_length=3, max_length=300)
    description: str = Field(default="", max_length=10_000)
    priority: Priority = "normal"
    requester_email: Optional[EmailStr] = None
    assignee_user_id: Optional[str] = Field(default=None, max_length=32)


class TicketUpdate(BaseModel):
    # All optional: only provided fields are applied (PATCH semantics).
    subject: Optional[str] = Field(default=None, min_length=3, max_length=300)
    description: Optional[str] = Field(default=None, max_length=10_000)
    status: Optional[Status] = None
    priority: Optional[Priority] = None
    assignee_user_id: Optional[str] = Field(default=None, max_length=32)


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    subject: str
    description: str
    status: Status
    priority: Priority
    requester_email: Optional[EmailStr]
    assignee_user_id: Optional[str]
    created_by: str
    created_at: dt.datetime
    updated_at: dt.datetime


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)


class CommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    ticket_id: str
    author_user_id: str
    body: str
    created_at: dt.datetime
