"""Background event consumer for the notifications-service.

Subscribes to the ticket.* events on the shared event bus and records an in-app
:class:`Notification` for the ticket's tenant. Email delivery is *faked* in demo
mode (logged, never sent) so the whole system runs with no SMTP credentials.

``register_subscriptions`` is called both by the service lifespan (with the real
Redis bus) and by the test fixtures (with the in-memory bus).
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.events import EventBus, Handler
from shared.topics import ALL_TICKET_TOPICS

from .models import Notification

logger = logging.getLogger("tenantforge.notifications")
email_logger = logging.getLogger("tenantforge.notifications.email")


def _render(payload: dict) -> tuple[str, str, str]:
    """Map an event payload to (type, title, body) for a notification."""
    event = payload.get("event", "updated")
    subject = payload.get("subject", "(no subject)")
    ticket_short = (payload.get("ticket_id") or "")[:8]
    status = payload.get("status", "open")
    if event == "created":
        return (
            "ticket.created",
            f"New ticket: {subject}",
            f"Ticket #{ticket_short} was created (status: {status}).",
        )
    if event == "commented":
        return (
            "ticket.commented",
            f"New comment: {subject}",
            f"A new comment was added to ticket #{ticket_short}.",
        )
    return (
        "ticket.updated",
        f"Ticket updated: {subject}",
        f"Ticket #{ticket_short} is now '{status}'.",
    )


def _send_fake_email(payload: dict, title: str, *, demo: bool) -> None:
    if demo:
        email_logger.info(
            "[demo-email] tenant=%s ticket=%s :: %s",
            payload.get("tenant_id"),
            payload.get("ticket_id"),
            title,
        )
    # A real implementation would enqueue an SMTP/provider send here.


def make_event_handler(
    sessionmaker: async_sessionmaker[AsyncSession], *, email_demo: bool = True
) -> Handler:
    async def handler(payload: dict) -> None:
        tenant_id = payload.get("tenant_id")
        if not tenant_id:
            logger.warning("dropping event without tenant_id: %s", payload)
            return

        ntype, title, body = _render(payload)
        async with sessionmaker() as session:
            session.add(
                Notification(
                    tenant_id=tenant_id,
                    user_id=payload.get("assignee_user_id"),
                    type=ntype,
                    title=title,
                    body=body,
                    ticket_id=payload.get("ticket_id"),
                )
            )
            await session.commit()

        _send_fake_email(payload, title, demo=email_demo)

    return handler


async def register_subscriptions(
    bus: EventBus,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    email_demo: bool = True,
) -> None:
    handler = make_event_handler(sessionmaker, email_demo=email_demo)
    for topic in ALL_TICKET_TOPICS:
        await bus.subscribe(topic, handler)
