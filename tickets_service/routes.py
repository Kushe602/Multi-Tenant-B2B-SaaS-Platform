"""tickets-service HTTP routes.

Tenant isolation is enforced in exactly one place -- :func:`_owned_ticket` -- and
every read/write path goes through it. ``principal.tenant_id`` comes from the
verified JWT, so no request can reach another tenant's data.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.deps import get_event_bus, get_principal, get_session
from shared.events import EventBus
from shared.security import Principal
from shared.topics import (
    TICKET_ASSIGNED,
    TICKET_COMMENTED,
    TICKET_CREATED,
    TICKET_UPDATED,
)

from .models import (
    PRIORITY_ORDER,
    STATUS_OPEN,
    STATUS_ORDER,
    Comment,
    Ticket,
)
from .schemas import (
    CommentCreate,
    CommentOut,
    Priority,
    Status,
    TicketAssign,
    TicketCreate,
    TicketOut,
    TicketStats,
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
        "category": ticket.category,
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
        category=payload.category,
        tags=payload.tags,
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
    priority_filter: Priority | None = None,
    assignee: str | None = None,
    q: str | None = None,
) -> list[TicketOut]:
    # Every filter is applied *on top of* the mandatory tenant scope, so no
    # combination of query params can ever widen the result past this tenant.
    query = select(Ticket).where(Ticket.tenant_id == principal.tenant_id)
    if status_filter is not None:
        query = query.where(Ticket.status == status_filter)
    if priority_filter is not None:
        query = query.where(Ticket.priority == priority_filter)
    if assignee is not None:
        query = query.where(Ticket.assignee_user_id == assignee)
    if q:
        pattern = f"%{q.strip()}%"
        query = query.where(
            or_(
                Ticket.subject.ilike(pattern),
                Ticket.description.ilike(pattern),
                Ticket.category.ilike(pattern),
            )
        )
    query = query.order_by(Ticket.created_at.desc())
    tickets = await session.scalars(query)
    return [TicketOut.model_validate(t) for t in tickets]


@router.get("/tickets/stats", response_model=TicketStats)
async def ticket_stats(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> TicketStats:
    """Tenant-scoped aggregate counts for the dashboard (open/pending/... & priority)."""
    by_status = dict.fromkeys(STATUS_ORDER, 0)
    status_rows = await session.execute(
        select(Ticket.status, func.count())
        .where(Ticket.tenant_id == principal.tenant_id)
        .group_by(Ticket.status)
    )
    for name, count in status_rows:
        if name in by_status:
            by_status[name] = int(count)

    by_priority = dict.fromkeys(PRIORITY_ORDER, 0)
    priority_rows = await session.execute(
        select(Ticket.priority, func.count())
        .where(Ticket.tenant_id == principal.tenant_id)
        .group_by(Ticket.priority)
    )
    for name, count in priority_rows:
        if name in by_priority:
            by_priority[name] = int(count)

    return TicketStats(
        total=sum(by_status.values()),
        by_status=by_status,
        by_priority=by_priority,
    )


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


@router.post("/tickets/{ticket_id}/assign", response_model=TicketOut)
async def assign_ticket(
    ticket_id: str,
    payload: TicketAssign,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
    bus: EventBus = Depends(get_event_bus),
) -> TicketOut:
    """Assign (or unassign) a ticket to a user in the tenant; emits ``ticket.assigned``.

    Tenant membership of the assignee is validated at the gateway (which can see
    the identity service); the ticket itself is always tenant-scoped here.
    """
    ticket = await _owned_ticket(session, principal, ticket_id)
    ticket.assignee_user_id = payload.assignee_user_id
    await session.commit()

    await bus.publish(TICKET_ASSIGNED, _event_payload(ticket, principal, "assigned"))
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

    if payload.parent_id is not None:
        # A reply's parent must be a comment on the same ticket and tenant.
        parent = await session.scalar(
            select(Comment).where(
                Comment.id == payload.parent_id,
                Comment.ticket_id == ticket.id,
                Comment.tenant_id == principal.tenant_id,
            )
        )
        if parent is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="parent comment not found"
            )

    comment = Comment(
        tenant_id=principal.tenant_id,
        ticket_id=ticket.id,
        parent_id=payload.parent_id,
        author_user_id=principal.user_id,
        body=payload.body,
    )
    session.add(comment)
    await session.commit()

    await bus.publish(TICKET_COMMENTED, _event_payload(ticket, principal, "commented"))
    return CommentOut.model_validate(comment)
