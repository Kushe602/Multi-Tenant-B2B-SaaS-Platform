"""Security primitives: password hashing (bcrypt) and JWT (PyJWT).

* Passwords are hashed with bcrypt. bcrypt only considers the first 72 bytes of
  input, so we truncate deterministically to keep ``hash``/``verify`` consistent
  and to avoid a ``ValueError`` on very long inputs.
* Session tokens are stateless HS256 JWTs signed with ``SECRET_KEY``. The claims
  carry the tenant, so tenant scoping downstream is derived from a *signed*
  token rather than any client-supplied value.
"""

from __future__ import annotations

import datetime as dt
import uuid

import bcrypt
import jwt
from pydantic import BaseModel, EmailStr

_BCRYPT_MAX_BYTES = 72


class Principal(BaseModel):
    """The authenticated caller, reconstructed from a verified JWT."""

    user_id: str
    tenant_id: str
    role: str
    email: EmailStr


class TokenError(Exception):
    """Raised when a JWT is missing, malformed, expired or has a bad signature."""


def hash_password(password: str) -> str:
    payload = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(payload, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    payload = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    try:
        return bcrypt.checkpw(payload, hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(
    *,
    user_id: str,
    tenant_id: str,
    role: str,
    email: str,
    secret_key: str,
    algorithm: str = "HS256",
    expires_minutes: int = 60 * 24,
) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    claims = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "email": email,
        "iat": now,
        "exp": now + dt.timedelta(minutes=expires_minutes),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, secret_key, algorithm=algorithm)


def decode_token(token: str, *, secret_key: str, algorithms: list[str] | None = None) -> Principal:
    try:
        claims = jwt.decode(token, secret_key, algorithms=algorithms or ["HS256"])
    except jwt.PyJWTError as exc:  # expired, bad signature, malformed, ...
        raise TokenError(str(exc)) from exc

    try:
        return Principal(
            user_id=claims["sub"],
            tenant_id=claims["tenant_id"],
            role=claims["role"],
            email=claims["email"],
        )
    except (KeyError, ValueError) as exc:
        raise TokenError(f"token missing required claims: {exc}") from exc
