"""tickets-service HTTP routes.

Tenant isolation is enforced in exactly one place -- :func:`_owned_ticket` -- and
every read/write path goes through it. ``principal.tenant_id`` comes from the
verified JWT, so no request can reach another tenant's data.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.deps import get_event_bus, get_principal, get_session
from shared.events import EventBus
from shared.security import Principal
from shared.topics import TICKET_COMMENTED, TICKET_CREATED, TICKET_UPDATED

from .models import STATUS_OPEN, Comment, Ticket
from .schemas import (
    CommentCreate,
    CommentOut,
    Status,
    TicketCreate,
    TicketOut,
    TicketUpdate,
)

router = APIRouter()


def _event_payload(ticket: Ticket, principal: Principal, event: str) -> dict:
    """Build a JSON-serialisable cross-service event payload."""
    return {
        "event": event,
        "tenant_id": ticket.tenant_id,
        "ticket_id": ticket.id,
        "subject": ticket.subject,
        "status": ticket.status,
        "priority": ticket.priority,
        "assignee_user_id": ticket.assignee_user_id,
        "actor_user_id": principal.user_id,
    }


async def _owned_ticket(session: AsyncSession, principal: Principal, ticket_id: str) -> Ticket:
    """Fetch a ticket **scoped to the caller's tenant** or raise 404.

    Returning 404 (not 403) for another tenant's id avoids leaking whether that
    id exists at all.
    """
    ticket = await session.scalar(
        select(Ticket).where(
            Ticket.id == ticket_id,
            Ticket.tenant_id == principal.tenant_id,
        )
    )
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ticket not found")
    return ticket


@router.post("/tickets", response_model=TicketOut, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    payload: TicketCreate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
    bus: EventBus = Depends(get_event_bus),
) -> TicketOut:
    ticket = Ticket(
        tenant_id=principal.tenant_id,
        subject=payload.subject,
        description=payload.description,
        priority=payload.priority,
        requester_email=str(payload.requester_email) if payload.requester_email else None,
        assignee_user_id=payload.assignee_user_id,
        created_by=principal.user_id,
        status=STATUS_OPEN,
    )
    session.add(ticket)
    await session.commit()

    await bus.publish(TICKET_CREATED, _event_payload(ticket, principal, "created"))
    return TicketOut.model_validate(ticket)


@router.get("/tickets", response_model=list[TicketOut])
async def list_tickets(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
    status_filter: Status | None = None,
) -> list[TicketOut]:
    query = select(Ticket).where(Ticket.tenant_id == principal.tenant_id)
    if status_filter is not None:
        query = query.where(Ticket.status == status_filter)
    query = query.order_by(Ticket.created_at.desc())
    tickets = await session.scalars(query)
    return [TicketOut.model_validate(t) for t in tickets]


@router.get("/tickets/{ticket_id}", response_model=TicketOut)
async def get_ticket(
    ticket_id: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> TicketOut:
    ticket = await _owned_ticket(session, principal, ticket_id)
    return TicketOut.model_validate(ticket)


@router.patch("/tickets/{ticket_id}", response_model=TicketOut)
async def update_ticket(
    ticket_id: str,
    payload: TicketUpdate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
    bus: EventBus = Depends(get_event_bus),
) -> TicketOut:
    ticket = await _owned_ticket(session, principal, ticket_id)

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="no fields to update"
        )
    for field, value in changes.items():
        setattr(ticket, field, value)
    await session.commit()

    await bus.publish(TICKET_UPDATED, _event_payload(ticket, principal, "updated"))
    return TicketOut.model_validate(ticket)


@router.get("/tickets/{ticket_id}/comments", response_model=list[CommentOut])
async def list_comments(
    ticket_id: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> list[CommentOut]:
    await _owned_ticket(session, principal, ticket_id)  # 404 if not in tenant
    comments = await session.scalars(
        select(Comment)
        .where(Comment.ticket_id == ticket_id, Comment.tenant_id == principal.tenant_id)
        .order_by(Comment.created_at)
    )
    return [CommentOut.model_validate(c) for c in comments]


@router.post(
    "/tickets/{ticket_id}/comments",
    response_model=CommentOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_comment(
    ticket_id: str,
    payload: CommentCreate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
    bus: EventBus = Depends(get_event_bus),
) -> CommentOut:
    ticket = await _owned_ticket(session, principal, ticket_id)
    comment = Comment(
        tenant_id=principal.tenant_id,
        ticket_id=ticket.id,
        author_user_id=principal.user_id,
        body=payload.body,
    )
    session.add(comment)
    await session.commit()

    await bus.publish(TICKET_COMMENTED, _event_payload(ticket, principal, "commented"))
    return CommentOut.model_validate(comment)
