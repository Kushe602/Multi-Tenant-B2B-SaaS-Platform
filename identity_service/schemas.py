"""Pydantic v2 request/response schemas for the identity-service."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Role = Literal["owner", "agent"]


class SignupRequest(BaseModel):
    org_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(default="", max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    email: EmailStr
    name: str
    role: Role
    is_active: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
    tenant: TenantOut


class InviteCreate(BaseModel):
    email: EmailStr
    role: Role = "agent"


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    email: EmailStr
    role: Role
    token: str
    accepted: bool
    expires_at: dt.datetime


class InviteAccept(BaseModel):
    token: str = Field(min_length=8, max_length=64)
    name: str = Field(default="", max_length=200)
    password: str = Field(min_length=8, max_length=128)
