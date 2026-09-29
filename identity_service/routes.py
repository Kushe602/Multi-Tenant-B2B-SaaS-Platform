"""Identity-service HTTP routes: signup, login, RBAC and tenant-scoped invites."""

from __future__ import annotations

import datetime as dt
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config import BaseServiceSettings
from shared.deps import get_principal, get_session, get_settings, require_internal, require_role
from shared.security import Principal, create_access_token, hash_password, verify_password

from .models import ROLE_AGENT, ROLE_OWNER, Invite, Tenant, User
from .schemas import (
    InviteAccept,
    InviteCreate,
    InviteOut,
    LoginRequest,
    SignupRequest,
    TenantOut,
    TokenResponse,
    UserOut,
)

router = APIRouter()

_INVITE_TTL = dt.timedelta(days=7)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "tenant"


async def _unique_slug(session: AsyncSession, name: str) -> str:
    base = _slugify(name)[:48]
    slug = base
    while await session.scalar(select(Tenant.id).where(Tenant.slug == slug)) is not None:
        slug = f"{base}-{secrets.token_hex(3)}"
    return slug


def _issue_token(settings: BaseServiceSettings, user: User) -> str:
    return create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        role=user.role,
        email=user.email,
        secret_key=settings.secret_key,
        algorithm=settings.jwt_algorithm,
        expires_minutes=settings.jwt_expire_minutes,
    )


@router.post(
    "/auth/signup",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_internal)],
)
async def signup(
    payload: SignupRequest,
    session: AsyncSession = Depends(get_session),
    settings: BaseServiceSettings = Depends(get_settings),
) -> TokenResponse:
    """Register a new organization (tenant) and its first user (an owner)."""
    email = payload.email.lower()
    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email already registered")

    tenant = Tenant(name=payload.org_name, slug=await _unique_slug(session, payload.org_name))
    session.add(tenant)
    await session.flush()  # allocate tenant.id before creating the user

    user = User(
        tenant_id=tenant.id,
        email=email,
        name=payload.name,
        password_hash=hash_password(payload.password),
        role=ROLE_OWNER,
    )
    session.add(user)
    await session.commit()

    return TokenResponse(
        access_token=_issue_token(settings, user),
        user=UserOut.model_validate(user),
        tenant=TenantOut.model_validate(tenant),
    )


@router.post("/auth/login", response_model=TokenResponse, dependencies=[Depends(require_internal)])
async def login(
    payload: LoginRequest,
    session: AsyncSession = Depends(get_session),
    settings: BaseServiceSettings = Depends(get_settings),
) -> TokenResponse:
    user = await session.scalar(select(User).where(User.email == payload.email.lower()))
    # Verify a hash even when the user is missing to blunt timing oracles.
    placeholder = "$2b$12$" + "." * 53
    if user is None or not user.is_active:
        verify_password(payload.password, placeholder)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials"
        )
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials"
        )

    tenant = await session.get(Tenant, user.tenant_id)
    return TokenResponse(
        access_token=_issue_token(settings, user),
        user=UserOut.model_validate(user),
        tenant=TenantOut.model_validate(tenant),
    )


@router.get("/me", response_model=UserOut)
async def me(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    user = await session.scalar(
        select(User).where(User.id == principal.user_id, User.tenant_id == principal.tenant_id)
    )
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    return UserOut.model_validate(user)


@router.get("/users", response_model=list[UserOut])
async def list_users(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> list[UserOut]:
    # Tenant-scoped: a caller only ever sees users from their own tenant.
    users = await session.scalars(
        select(User).where(User.tenant_id == principal.tenant_id).order_by(User.created_at)
    )
    return [UserOut.model_validate(u) for u in users]


@router.post(
    "/invites",
    response_model=InviteOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_invite(
    payload: InviteCreate,
    principal: Principal = Depends(require_role(ROLE_OWNER)),
    session: AsyncSession = Depends(get_session),
) -> InviteOut:
    """Owner-only: create a tenant-scoped invite (email delivery is faked)."""
    invite = Invite(
        tenant_id=principal.tenant_id,
        email=payload.email.lower(),
        role=payload.role,
        token=secrets.token_urlsafe(24),
        expires_at=dt.datetime.now(dt.timezone.utc) + _INVITE_TTL,
    )
    session.add(invite)
    await session.commit()
    return InviteOut.model_validate(invite)


@router.get("/invites", response_model=list[InviteOut])
async def list_invites(
    principal: Principal = Depends(require_role(ROLE_OWNER)),
    session: AsyncSession = Depends(get_session),
) -> list[InviteOut]:
    invites = await session.scalars(
        select(Invite).where(Invite.tenant_id == principal.tenant_id).order_by(Invite.created_at)
    )
    return [InviteOut.model_validate(i) for i in invites]


@router.post(
    "/invites/accept",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_internal)],
)
async def accept_invite(
    payload: InviteAccept,
    session: AsyncSession = Depends(get_session),
    settings: BaseServiceSettings = Depends(get_settings),
) -> TokenResponse:
    now = dt.datetime.now(dt.timezone.utc)
    invite = await session.scalar(select(Invite).where(Invite.token == payload.token))
    if invite is None or invite.accepted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invite not found")
    # SQLite may return naive datetimes; treat them as UTC for comparison.
    expires = invite.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=dt.timezone.utc)
    if expires < now:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="invite expired")

    if await session.scalar(select(User).where(User.email == invite.email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email already registered")

    role = invite.role if invite.role in (ROLE_OWNER, ROLE_AGENT) else ROLE_AGENT
    user = User(
        tenant_id=invite.tenant_id,
        email=invite.email,
        name=payload.name,
        password_hash=hash_password(payload.password),
        role=role,
    )
    invite.accepted = True
    session.add(user)
    await session.commit()

    tenant = await session.get(Tenant, user.tenant_id)
    return TokenResponse(
        access_token=_issue_token(settings, user),
        user=UserOut.model_validate(user),
        tenant=TenantOut.model_validate(tenant),
    )
