"""tickets-service ORM models: Ticket and Comment.

The isolation invariant of the whole platform lives here: **every** row carries
``tenant_id`` and **every** query in :mod:`tickets_service.routes` filters on the
caller's tenant. ``tenant_id`` always originates from the verified JWT, never
from request input, so cross-tenant reads/writes are structurally impossible.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

STATUS_OPEN = "open"
STATUS_PENDING = "pending"
STATUS_CLOSED = "closed"
VALID_STATUSES = frozenset({STATUS_OPEN, STATUS_PENDING, STATUS_CLOSED})

PRIORITY_LOW = "low"
PRIORITY_NORMAL = "normal"
PRIORITY_HIGH = "high"
VALID_PRIORITIES = frozenset({PRIORITY_LOW, PRIORITY_NORMAL, PRIORITY_HIGH})


def _uuid() -> str:
    return uuid.uuid4().hex


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        # Composite index: listing tickets is always "by tenant, newest first".
        Index("ix_tickets_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=STATUS_OPEN, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default=PRIORITY_NORMAL, nullable=False)
    requester_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    assignee_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    comments: Mapped[list["Comment"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    ticket_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tickets.id", ondelete="CASCADE"), index=True, nullable=False
    )
    author_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    ticket: Mapped["Ticket"] = relationship(back_populates="comments")
