"""A4d — `python -m backend.push_selftest`: a credential-only check for FCM push.

Same injected-transport convention as test_push_fcm_v1.py — no network, no real
service account. Proves the module's OWN logic (parse → mint → validate_only send →
interpret the result) without needing PUSH_FCM_SERVICE_ACCOUNT_JSON to be set for
real — that half (A4c/A4d against the live credential) is an operator step, tracked
in PENDING.md FL11 as deferred until the Firebase iOS/Android bundle IDs are locked.
"""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import patch

import pytest

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from backend import push as push_mod  # noqa: E402
from backend import push_selftest  # noqa: E402

PROJECT = "example-fcm-project"
TOKEN_URI = "https://oauth2.example/token"


@pytest.fixture(scope="module")
def service_account() -> dict:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return {
        "type": "service_account",
        "project_id": PROJECT,
        "client_email": "pusher@example.iam.gserviceaccount.example",
        "private_key": pem,
        "token_uri": TOKEN_URI,
    }


@pytest.fixture(autouse=True)
def _fresh_token_cache():
    push_mod.reset_token_cache()
    yield
    push_mod.reset_token_cache()


class _FakeTransport:
    def __init__(self, *, token_status=200, validate_status=200, validate_body=None) -> None:
        self.token_status = token_status
        self.validate_status = validate_status
        self.validate_body = validate_body if validate_body is not None else {"name": "x"}
        self.calls: list[str] = []

    async def __call__(self, method, url, *, headers=None, json_body=None, data=None):
        self.calls.append(url)
        if url == TOKEN_URI:
            if self.token_status != 200:
                return self.token_status, {"error": "invalid_grant"}
            return 200, {"access_token": "ya29.fake", "expires_in": 3600}
        assert json_body["validate_only"] is True
        assert json_body["message"]["token"] == push_selftest._VALIDATE_ONLY_TOKEN
        return self.validate_status, self.validate_body


def _env(service_account: dict) -> dict:
    return {"PUSH_PROVIDER": "fcm", "PUSH_FCM_SERVICE_ACCOUNT_JSON": json.dumps(service_account)}


def test_ok_when_validate_only_accepts_the_message(service_account):
    fake = _FakeTransport(validate_status=200)
    with patch.dict(os.environ, _env(service_account), clear=False):
        result = asyncio.run(push_selftest._check(fake))
    assert result == "OK"
    assert fake.calls == [
        TOKEN_URI,
        f"https://fcm.googleapis.com/v1/projects/{PROJECT}/messages:send",
    ]


def test_ok_when_the_dummy_token_is_rejected_as_invalid_argument(service_account):
    """The self-test proves auth succeeded, not that the placeholder token is real —
    FCM only reaches INVALID_ARGUMENT after accepting the Bearer credential."""
    fake = _FakeTransport(
        validate_status=400, validate_body={"error": {"status": "INVALID_ARGUMENT"}}
    )
    with patch.dict(os.environ, _env(service_account), clear=False):
        result = asyncio.run(push_selftest._check(fake))
    assert result == "OK"


def test_fails_when_no_service_account_is_configured():
    with patch.dict(os.environ, {"PUSH_FCM_SERVICE_ACCOUNT_JSON": ""}, clear=False):
        result = asyncio.run(push_selftest._check())
    assert "no usable" in result


def test_fails_when_the_oauth_exchange_is_rejected(service_account):
    fake = _FakeTransport(token_status=401)
    with patch.dict(os.environ, _env(service_account), clear=False):
        result = asyncio.run(push_selftest._check(fake))
    assert result == "OAuth2 token exchange failed"


def test_fails_when_fcm_rejects_for_an_unrelated_reason(service_account):
    fake = _FakeTransport(
        validate_status=403, validate_body={"error": {"status": "PERMISSION_DENIED"}}
    )
    with patch.dict(os.environ, _env(service_account), clear=False):
        result = asyncio.run(push_selftest._check(fake))
    assert "403" in result


def test_main_prints_exactly_the_operator_facing_line(service_account, capsys):
    fake = _FakeTransport(validate_status=200)
    with patch.dict(os.environ, _env(service_account), clear=False):
        code = push_selftest.main(fake)
    assert code == 0
    assert capsys.readouterr().out.strip() == "FCM credentials OK"


def test_main_exits_nonzero_and_never_leaks_a_secret_on_failure(capsys):
    with patch.dict(os.environ, {"PUSH_FCM_SERVICE_ACCOUNT_JSON": ""}, clear=False):
        code = push_selftest.main()
    assert code == 1
    out = capsys.readouterr().out
    assert "FCM credentials FAILED" in out
    assert "private_key" not in out and "BEGIN" not in out


async def _fcm_unreachable(method, url, *, headers=None, json_body=None, data=None):
    if url == TOKEN_URI:
        return 200, {"access_token": "ya29.fake", "expires_in": 3600}
    raise TimeoutError("fcm.googleapis.com timed out")


def test_a_network_failure_on_the_send_is_one_failure_line(service_account, capsys):
    """Before this, a timeout on the validate_only send escaped _check, so the operator got
    a traceback and no result line at all."""
    with (
        patch.dict(os.environ, _env(service_account), clear=False),
        patch.object(push_mod, "_RETRY_BACKOFF_S", 0),
    ):
        code = push_selftest.main(_fcm_unreachable)
    assert code == 1
    assert capsys.readouterr().out.splitlines() == [
        "FCM credentials FAILED: FCM validate_only request failed (network error)"
    ]


def test_the_cli_prints_one_line_and_no_traceback_when_google_is_unreachable(service_account):
    """The real command, real transport, pointed at a closed local port: stdout is the one
    result line and stderr carries no traceback from push's own logging."""
    import socket
    import subprocess
    import sys
    from pathlib import Path

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # closed again on exit: connections are refused
    account = {**service_account, "token_uri": f"https://127.0.0.1:{port}/token"}
    env = {**os.environ, **_env(account)}
    proc = subprocess.run(
        [sys.executable, "-m", "backend.push_selftest"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 1
    assert proc.stdout.splitlines() == ["FCM credentials FAILED: OAuth2 token exchange failed"]
    assert "Traceback" not in proc.stderr, proc.stderr
