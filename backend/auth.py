"""JWT authentication and password hashing for the backend API.

Secrets come from Doppler (never hardcoded):
  BACKEND_JWT_SECRET        — HS256 signing key (generate: openssl rand -hex 32)
  BACKEND_JWT_EXPIRE_MINUTES — access token TTL (default: 60)
  BACKEND_REFRESH_EXPIRE_DAYS — refresh token TTL (default: 30)

JWT payload:
  sub          — user ID (UUID string)
  workspace_id — the user's workspace ID
  type         — "access" | "refresh"
  exp / iat    — standard claims

Password hashing uses bcrypt directly. The hash is stored in users.password_hash.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Literal

import bcrypt
import jwt

_SECRET = lambda: os.environ["BACKEND_JWT_SECRET"]  # noqa: E731 — evaluated lazily
_ACCESS_TTL = lambda: int(os.getenv("BACKEND_JWT_EXPIRE_MINUTES", "60"))  # noqa: E731
_REFRESH_TTL = lambda: int(os.getenv("BACKEND_REFRESH_EXPIRE_DAYS", "30"))  # noqa: E731
_ALGO = "HS256"


# ── passwords ─────────────────────────────────────────────────────────────────


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ── tokens ────────────────────────────────────────────────────────────────────


def _make_token(
    user_id: str,
    workspace_id: str,
    kind: Literal["access", "refresh"],
    ttl: timedelta,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "workspace_id": workspace_id,
        "type": kind,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, _SECRET(), algorithm=_ALGO)


def create_access_token(user_id: str, workspace_id: str) -> str:
    return _make_token(
        user_id,
        workspace_id,
        "access",
        timedelta(minutes=_ACCESS_TTL()),
    )


def create_refresh_token(user_id: str, workspace_id: str) -> str:
    return _make_token(
        user_id,
        workspace_id,
        "refresh",
        timedelta(days=_REFRESH_TTL()),
    )


def decode_token(token: str, expected_type: str = "access") -> dict:
    """Decode and validate a JWT. Raises jwt.InvalidTokenError on failure."""
    payload = jwt.decode(token, _SECRET(), algorithms=[_ALGO])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(
            f"expected token type {expected_type!r}, got {payload.get('type')!r}"
        )
    return payload


def token_predates_password_change(payload: dict, password_changed_at: datetime | None) -> bool:
    """True if this token was issued before the user's last password change.

    ``password_changed_at`` is NULL for a user who has never changed their password
    since registration, so their tokens are always current. Otherwise a token whose
    ``iat`` (epoch seconds) predates the change is stale and must be rejected — the
    signature check alone can't tell, since the token is cryptographically valid.
    """
    if password_changed_at is None:
        return False
    return payload["iat"] < password_changed_at.timestamp()


def token_pair(user_id: str, workspace_id: str) -> dict:
    """Return both tokens in the API response shape."""
    return {
        "access_token": create_access_token(user_id, workspace_id),
        "refresh_token": create_refresh_token(user_id, workspace_id),
        "token_type": "bearer",
    }
