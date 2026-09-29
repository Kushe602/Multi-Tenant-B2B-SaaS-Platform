"""notifications-service HTTP routes (read + mark-as-read)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.deps import get_principal, get_session
from shared.security import Principal

from .models import Notification
from .schemas import NotificationOut

router = APIRouter()


@router.get("/notifications", response_model=list[NotificationOut])
async def list_notifications(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
    unread_only: bool = False,
    limit: int = 50,
) -> list[NotificationOut]:
    query = select(Notification).where(Notification.tenant_id == principal.tenant_id)
    if unread_only:
        query = query.where(Notification.read.is_(False))
    query = query.order_by(Notification.created_at.desc()).limit(max(1, min(limit, 200)))
    rows = await session.scalars(query)
    return [NotificationOut.model_validate(n) for n in rows]


@router.get("/notifications/unread_count")
async def unread_count(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    count = await session.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.tenant_id == principal.tenant_id, Notification.read.is_(False))
    )
    return {"unread": int(count or 0)}


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
async def mark_read(
    notification_id: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> NotificationOut:
    note = await session.scalar(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.tenant_id == principal.tenant_id,
        )
    )
    if note is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="notification not found")
    note.read = True
    await session.commit()
    return NotificationOut.model_validate(note)
