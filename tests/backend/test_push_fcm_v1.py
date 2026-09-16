"""A6 — FCM HTTP v1 push: no-op when unset, contentless payload, OAuth2 token
minting (mocked — no network), dead-token cleanup, and the invariant that a
transient 5xx never prunes a live device.

Convention: no pytest-asyncio (asyncio.run bodies); the transport is injected so
nothing here touches the network.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from backend import push as push_mod  # noqa: E402

WS_ID = str(uuid.uuid4())
RUN_ID = str(uuid.uuid4())
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


class FakeFcm:
    """Injected transport: mints tokens, records sends, scriptable per-token replies."""

    def __init__(self, replies: dict[str, tuple[int, dict]] | None = None) -> None:
        self.sends: list[dict] = []
        self.token_mints = 0
        self.replies = replies or {}

    async def __call__(self, method, url, *, headers, json_body=None, data=None):
        if url == TOKEN_URI:
            self.token_mints += 1
            return 200, {"access_token": "ya29.fake-access-token", "expires_in": 3600}
        self.sends.append({"url": url, "headers": headers, "body": json_body})
        device = (json_body or {}).get("message", {}).get("token", "")
        return self.replies.get(device, (200, {"name": "projects/x/messages/1"}))


def _pool_with_tokens(tokens: list[tuple[str, str]]):
    conn = AsyncMock()
    conn.fetch.return_value = [{"token": t, "platform": p} for t, p in tokens]
    conn.execute = AsyncMock()
    pool = MagicMock()

    @asynccontextmanager
    async def _scope(p, wid):
        yield conn

    return pool, conn, _scope


def _send(pool, scope, transport, env: dict):
    with patch("backend.push.workspace_scope", scope), patch.dict(os.environ, env, clear=False):
        return asyncio.run(
            push_mod.send_gate_push(pool, WS_ID, RUN_ID, "⟦GATE:plan⟧", transport=transport)
        )


def test_noop_when_provider_unset(service_account):
    """Provider unset ⇒ 0 dispatched and NOT a single outbound call."""
    pool, _conn, scope = _pool_with_tokens([("device-1", "fcm")])
    fake = FakeFcm()
    count = _send(
        pool,
        scope,
        fake,
        {"PUSH_PROVIDER": "", "PUSH_FCM_SERVICE_ACCOUNT_JSON": json.dumps(service_account)},
    )
    assert count == 0
    assert fake.sends == [] and fake.token_mints == 0


def test_noop_when_service_account_missing():
    """Provider set but no credential ⇒ still no call (fail closed, never crash)."""
    pool, _conn, scope = _pool_with_tokens([("device-1", "fcm")])
    fake = FakeFcm()
    count = _send(pool, scope, fake, {"PUSH_PROVIDER": "fcm", "PUSH_FCM_SERVICE_ACCOUNT_JSON": ""})
    assert count == 0 and fake.sends == []


def test_v1_endpoint_payload_is_contentless(service_account):
    """The send targets the HTTP v1 endpoint with a Bearer token, and the payload
    carries the run id + gate kind ONLY — never gate content."""
    pool, _conn, scope = _pool_with_tokens([("device-1", "fcm")])
    fake = FakeFcm()
    count = _send(
        pool,
        scope,
        fake,
        {"PUSH_PROVIDER": "fcm", "PUSH_FCM_SERVICE_ACCOUNT_JSON": json.dumps(service_account)},
    )

    assert count == 1
    (sent,) = fake.sends
    assert sent["url"] == f"https://fcm.googleapis.com/v1/projects/{PROJECT}/messages:send"
    assert sent["headers"]["Authorization"] == "Bearer ya29.fake-access-token"
    # Legacy API must be gone: no /fcm/send, no `key=` auth.
    assert "/fcm/send" not in sent["url"]
    assert not sent["headers"]["Authorization"].startswith("key=")

    message = sent["body"]["message"]
    assert message["token"] == "device-1"
    # data.gate is the stream's enum value, not the raw ⟦GATE:...⟧ sentinel — one gate
    # vocabulary for a client to parse everywhere.
    assert message["data"] == {"run_id": RUN_ID, "gate": "plan"}
    # Contentless: the only strings on the wire are the fixed labels + ids.
    blob = json.dumps(sent["body"])
    assert "pending_content" not in blob
    assert "⟦GATE" not in blob
    assert message["notification"] == {
        "title": "GTM — action needed",
        "body": "Plan ready for review",
    }


def test_publish_gate_payload_uses_the_stream_gate_vocabulary(service_account):
    """The publish gate was previously untested — same normalisation as the plan gate,
    covering the branch _build_payload takes for ⟦GATE:publish⟧."""
    pool, _conn, scope = _pool_with_tokens([("device-1", "fcm")])
    fake = FakeFcm()
    with (
        patch("backend.push.workspace_scope", scope),
        patch.dict(
            os.environ,
            {"PUSH_PROVIDER": "fcm", "PUSH_FCM_SERVICE_ACCOUNT_JSON": json.dumps(service_account)},
            clear=False,
        ),
    ):
        count = asyncio.run(
            push_mod.send_gate_push(pool, WS_ID, RUN_ID, "⟦GATE:publish⟧", transport=fake)
        )

    assert count == 1
    (sent,) = fake.sends
    message = sent["body"]["message"]
    assert message["data"] == {"run_id": RUN_ID, "gate": "publish"}
    assert message["notification"] == {
        "title": "GTM — action needed",
        "body": "Post ready to approve",
    }


def test_access_token_is_cached_across_sends(service_account):
    """One token mint serves many devices (and the next send reuses the cache)."""
    pool, _conn, scope = _pool_with_tokens([("d1", "fcm"), ("d2", "fcm"), ("d3", "fcm")])
    fake = FakeFcm()
    env = {"PUSH_PROVIDER": "fcm", "PUSH_FCM_SERVICE_ACCOUNT_JSON": json.dumps(service_account)}

    assert _send(pool, scope, fake, env) == 3
    assert _send(pool, scope, fake, env) == 3
    assert fake.token_mints == 1, "the access token must be cached, not minted per send"
    assert len(fake.sends) == 6


def test_dead_tokens_pruned_transient_errors_kept(service_account):
    """UNREGISTERED / INVALID_ARGUMENT prune; a 5xx must NEVER prune a live device."""
    pool, conn, scope = _pool_with_tokens(
        [("good", "fcm"), ("gone", "fcm"), ("bogus", "fcm"), ("flaky", "fcm")]
    )
    fake = FakeFcm(
        replies={
            "gone": (404, {"error": {"status": "UNREGISTERED"}}),
            "bogus": (400, {"error": {"status": "INVALID_ARGUMENT"}}),
            "flaky": (503, {"error": {"status": "UNAVAILABLE"}}),
        }
    )
    count = _send(
        pool,
        scope,
        fake,
        {"PUSH_PROVIDER": "fcm", "PUSH_FCM_SERVICE_ACCOUNT_JSON": json.dumps(service_account)},
    )

    assert count == 1  # only "good" delivered
    delete_calls = [c for c in conn.execute.await_args_list if "DELETE FROM push_tokens" in c[0][0]]
    assert len(delete_calls) == 1
    pruned = delete_calls[0][0][2]
    assert sorted(pruned) == ["bogus", "gone"]
    assert "flaky" not in pruned, "a transient 5xx must never prune a live device"


def test_non_fcm_platform_tokens_skipped(service_account):
    pool, _conn, scope = _pool_with_tokens([("ios-1", "apns"), ("android-1", "fcm")])
    fake = FakeFcm()
    count = _send(
        pool,
        scope,
        fake,
        {"PUSH_PROVIDER": "fcm", "PUSH_FCM_SERVICE_ACCOUNT_JSON": json.dumps(service_account)},
    )
    assert count == 1
    assert [s["body"]["message"]["token"] for s in fake.sends] == ["android-1"]


def test_service_account_accepts_a_file_path(service_account, tmp_path):
    path = tmp_path / "sa.json"
    path.write_text(json.dumps(service_account))
    pool, _conn, scope = _pool_with_tokens([("d1", "fcm")])
    fake = FakeFcm()
    count = _send(
        pool, scope, fake, {"PUSH_PROVIDER": "fcm", "PUSH_FCM_SERVICE_ACCOUNT_JSON": str(path)}
    )
    assert count == 1


def test_token_exchange_failure_dispatches_nothing(service_account):
    """A failed token mint must return 0, not raise into the gate path."""

    class BrokenAuth(FakeFcm):
        async def __call__(self, method, url, *, headers, json_body=None, data=None):
            if url == TOKEN_URI:
                return 401, {"error": "invalid_grant"}
            return await super().__call__(method, url, headers=headers, json_body=json_body)

    pool, _conn, scope = _pool_with_tokens([("d1", "fcm")])
    fake = BrokenAuth()
    count = _send(
        pool,
        scope,
        fake,
        {"PUSH_PROVIDER": "fcm", "PUSH_FCM_SERVICE_ACCOUNT_JSON": json.dumps(service_account)},
    )
    assert count == 0 and fake.sends == []


def test_no_tokens_registered_is_zero():
    pool, _conn, scope = _pool_with_tokens([])
    fake = FakeFcm()
    assert _send(pool, scope, fake, {"PUSH_PROVIDER": "fcm"}) == 0
    assert fake.token_mints == 0


# ── #240: the push names the real gate kind and the waiting step ─────────────────


def test_payload_carries_pack_gate_kind_node_id_and_matching_body():
    """A pack's enrollment gate must not push as "Plan ready for review" (the pre-#240
    behaviour flattened every pack gate to plan) — the kind, its label and the node id
    all reach the device, and the payload still carries no gate content."""
    payload = push_mod._build_payload(RUN_ID, "email_enroll", "sequence")
    assert payload["data"] == {"run_id": RUN_ID, "gate": "email_enroll", "node_id": "sequence"}
    assert payload["body"] == "Contacts ready to load into your sender"
    review = push_mod._build_payload(RUN_ID, "review", "capture")
    assert review["data"]["gate"] == "review" and review["body"] == "Ready for your review"


def test_payload_normalises_sentinels_and_unknown_kinds():
    assert push_mod._build_payload(RUN_ID, "⟦GATE:publish⟧")["data"] == {
        "run_id": RUN_ID,
        "gate": "publish",
    }
    assert push_mod._build_payload(RUN_ID, "something-new")["data"]["gate"] == "review"
