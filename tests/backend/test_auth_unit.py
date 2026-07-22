"""Unit tests for backend auth — JWT and password hashing.

These tests run without a DB or a running server (pure unit tests).
They verify the JWT contract: payloads, expiry, token type enforcement,
and password hashing round-trips.
"""

import os
import time
from datetime import UTC, datetime, timedelta

import jwt
import pytest

# Set a test secret before importing auth
os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-for-unit-tests-only")
os.environ.setdefault("BACKEND_JWT_EXPIRE_MINUTES", "60")
os.environ.setdefault("BACKEND_REFRESH_EXPIRE_DAYS", "30")

from backend.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    token_pair,
    token_predates_password_change,
    verify_password,
)

USER_ID = "usr-test-001"
WS_ID = "ws-test-001"


# ── password hashing ──────────────────────────────────────────────────────────


def test_hash_and_verify_roundtrip():
    h = hash_password("correct-horse-battery-staple")
    assert verify_password("correct-horse-battery-staple", h)


def test_wrong_password_rejected():
    h = hash_password("correct-password")
    assert not verify_password("wrong-password", h)


def test_hash_is_not_plaintext():
    plain = "my-secret"
    assert hash_password(plain) != plain


# ── access token ─────────────────────────────────────────────────────────────


def test_access_token_payload():
    token = create_access_token(USER_ID, WS_ID)
    payload = decode_token(token, expected_type="access")
    assert payload["sub"] == USER_ID
    assert payload["workspace_id"] == WS_ID
    assert payload["type"] == "access"


def test_access_token_rejects_refresh_type():
    token = create_refresh_token(USER_ID, WS_ID)
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token, expected_type="access")


# ── refresh token ─────────────────────────────────────────────────────────────


def test_refresh_token_payload():
    token = create_refresh_token(USER_ID, WS_ID)
    payload = decode_token(token, expected_type="refresh")
    assert payload["sub"] == USER_ID
    assert payload["workspace_id"] == WS_ID
    assert payload["type"] == "refresh"


def test_refresh_token_rejects_access_type():
    token = create_access_token(USER_ID, WS_ID)
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token, expected_type="refresh")


# ── token_pair ────────────────────────────────────────────────────────────────


def test_token_pair_shape():
    pair = token_pair(USER_ID, WS_ID)
    assert "access_token" in pair
    assert "refresh_token" in pair
    assert pair["token_type"] == "bearer"


# ── tampering ─────────────────────────────────────────────────────────────────


def test_tampered_token_rejected():
    token = create_access_token(USER_ID, WS_ID)
    tampered = token[:-4] + "xxxx"
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(tampered)


def test_wrong_secret_rejected():
    # Sign with a different secret
    payload = {
        "sub": USER_ID,
        "workspace_id": WS_ID,
        "type": "access",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    bad_token = jwt.encode(payload, "wrong-secret", algorithm="HS256")  # nosemgrep
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(bad_token)


# ── password-change invalidation (VULN-0003) ──────────────────────────────────


def test_token_current_when_never_changed():
    payload = decode_token(create_refresh_token(USER_ID, WS_ID), expected_type="refresh")
    # NULL password_changed_at → user never changed password → token is current.
    assert token_predates_password_change(payload, None) is False


def test_token_rejected_when_issued_before_change():
    payload = decode_token(create_refresh_token(USER_ID, WS_ID), expected_type="refresh")
    changed = datetime.fromtimestamp(payload["iat"], UTC) + timedelta(hours=1)
    assert token_predates_password_change(payload, changed) is True


def test_token_valid_when_issued_after_change():
    payload = decode_token(create_refresh_token(USER_ID, WS_ID), expected_type="refresh")
    changed = datetime.fromtimestamp(payload["iat"], UTC) - timedelta(hours=1)
    assert token_predates_password_change(payload, changed) is False
