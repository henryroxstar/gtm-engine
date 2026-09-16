"""Unit tests for Phase F.2 backend surface.

Tests for:
  - GET /v1/runs/{id} — pending_content field
  - POST /v1/runs/{id}/cancel
  - POST/GET/DELETE /v1/api-keys
  - PATCH /v1/account
  - DELETE /v1/account
  - PATCH /v1/workspace/cost-cap

All DB and pool calls are mocked — no live Postgres required.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-for-unit-tests-only-32x")
os.environ.setdefault("BACKEND_JWT_EXPIRE_MINUTES", "60")
os.environ.setdefault("BACKEND_REFRESH_EXPIRE_DAYS", "30")

from datetime import UTC

from backend.auth import create_access_token
from backend.errors import register_error_handlers
from backend.routers import account as account_router
from backend.routers import api_keys as api_keys_router
from backend.routers import runs as runs_router
from backend.routers import workspaces as workspaces_router
from tests.backend._protocol1 import (  # noqa: E402
    SCOPE_MODULES,
    patch_everywhere,
)

WS_ID = "00000000-0000-0000-0000-000000000001"
USER_ID = "00000000-0000-0000-0000-000000000002"
RUN_ID = "00000000-0000-0000-0000-000000000010"
KEY_ID = "00000000-0000-0000-0000-000000000020"

# The one envelope every federated credential failure renders, wherever it is raised.
_UNIFORM_FEDERATED_401 = {
    "code": "federated_token_invalid",
    "message": "Invalid federated token",
    "details": None,
}


def _access_token() -> str:
    return create_access_token(USER_ID, WS_ID)


def _auth_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {_access_token()}"}


def _make_app(*routers) -> FastAPI:
    app = FastAPI()
    for rtr in routers:
        app.include_router(rtr.router, prefix="/v1")
    app.state.pool = MagicMock()
    return app


# ── GET /v1/runs/{id} — pending_content ───────────────────────────────────────

_RUN_ROW = {
    "id": RUN_ID,
    "status": "awaiting_approval",
    "profile_name": "test",
    "output": None,
    "error": None,
    "pending_gate": "⟦GATE:publish⟧",
    "pending_content": "Here is the draft content for approval.",
}
_ENTITLEMENT_ROW = {"entitlement": "pro"}


class TestRunGetPendingContent:
    @pytest.fixture()
    def conn(self):
        c = AsyncMock()
        # fetchrow is called twice: once by require_auth (entitlement), once by get_run
        c.fetchrow.side_effect = [_ENTITLEMENT_ROW, _RUN_ROW]
        return c

    @pytest.fixture()
    def client(self, conn):
        app = _make_app(runs_router)

        @asynccontextmanager
        async def _scope(pool, workspace_id):
            yield conn

        with (
            patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope),
            patch("backend.deps.workspace_scope", _scope),
        ):
            with TestClient(app) as c:
                yield c

    def test_pending_content_returned(self, client):
        resp = client.get(f"/v1/runs/{RUN_ID}", headers=_auth_header())
        assert resp.status_code == 200
        body = resp.json()
        assert body["pending_content"] == "Here is the draft content for approval."
        assert body["status"] == "awaiting_approval"

    def test_pending_content_null_when_not_set(self, conn, client):
        conn.fetchrow.side_effect = [
            _ENTITLEMENT_ROW,
            {**_RUN_ROW, "status": "running", "pending_gate": None, "pending_content": None},
        ]
        resp = client.get(f"/v1/runs/{RUN_ID}", headers=_auth_header())
        assert resp.status_code == 200
        assert resp.json()["pending_content"] is None

    def test_run_not_found(self, conn, client):
        conn.fetchrow.side_effect = [_ENTITLEMENT_ROW, None]
        resp = client.get(f"/v1/runs/{RUN_ID}", headers=_auth_header())
        assert resp.status_code == 404


# ── POST /v1/runs/{id}/cancel ─────────────────────────────────────────────────


class TestCancelRun:
    # cancel_run calls fetchrow three times: require_auth (entitlement), the run status
    # check, then cancel()'s own conditional `UPDATE runs ... RETURNING id` — then
    # execute once more for the `UPDATE run_gates` close.

    @pytest.fixture()
    def conn(self):
        c = AsyncMock()
        c.execute.return_value = "UPDATE 1"
        c.fetchrow.side_effect = [_ENTITLEMENT_ROW, {"status": "running"}, {"id": RUN_ID}]
        return c

    @pytest.fixture()
    def client(self, conn):
        app = _make_app(runs_router)

        @asynccontextmanager
        async def _scope(pool, workspace_id):
            yield conn

        with (
            patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope),
            patch("backend.deps.workspace_scope", _scope),
        ):
            runs_router._cancelled_runs.clear()
            runs_router._gate_events.clear()
            runs_router._gate_decisions.clear()
            with TestClient(app) as c:
                yield c

    def test_cancel_running_run(self, client):
        resp = client.post(f"/v1/runs/{RUN_ID}/cancel", headers=_auth_header())
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "canceled"
        assert body["run_id"] == RUN_ID

    def test_cancel_pending_run(self, conn, client):
        conn.fetchrow.side_effect = [_ENTITLEMENT_ROW, {"status": "pending"}, {"id": RUN_ID}]
        resp = client.post(f"/v1/runs/{RUN_ID}/cancel", headers=_auth_header())
        assert resp.status_code == 200

    def test_cancel_awaiting_approval_fires_gate_event(self, conn, client):
        import asyncio

        conn.fetchrow.side_effect = [
            _ENTITLEMENT_ROW,
            {"status": "awaiting_approval"},
            {"id": RUN_ID},
        ]
        event = asyncio.Event()
        runs_router._gate_events[RUN_ID] = event

        resp = client.post(f"/v1/runs/{RUN_ID}/cancel", headers=_auth_header())
        assert resp.status_code == 200
        assert event.is_set()
        # A cancel is never represented as a gate decision (ST-15) — no fake `reject`
        # decision is written for the waiter to consume; it resolves via the terminal
        # row state instead (see backend/services/runs/lifecycle.py _wait_for_decision).
        assert RUN_ID not in runs_router._gate_decisions

    def test_cancel_terminal_ok_returns_409(self, conn, client):
        conn.fetchrow.side_effect = [_ENTITLEMENT_ROW, {"status": "ok"}]
        resp = client.post(f"/v1/runs/{RUN_ID}/cancel", headers=_auth_header())
        assert resp.status_code == 409

    def test_cancel_terminal_failed_returns_409(self, conn, client):
        conn.fetchrow.side_effect = [_ENTITLEMENT_ROW, {"status": "failed"}]
        resp = client.post(f"/v1/runs/{RUN_ID}/cancel", headers=_auth_header())
        assert resp.status_code == 409

    def test_cancel_terminal_rejected_returns_409(self, conn, client):
        conn.fetchrow.side_effect = [_ENTITLEMENT_ROW, {"status": "rejected"}]
        resp = client.post(f"/v1/runs/{RUN_ID}/cancel", headers=_auth_header())
        assert resp.status_code == 409

    def test_cancel_not_found_returns_404(self, conn, client):
        conn.fetchrow.side_effect = [_ENTITLEMENT_ROW, None]
        resp = client.post(f"/v1/runs/{RUN_ID}/cancel", headers=_auth_header())
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        resp = client.post(f"/v1/runs/{RUN_ID}/cancel")
        assert resp.status_code == 401


# ── POST /v1/api-keys ─────────────────────────────────────────────────────────


class TestCreateApiKey:
    @pytest.fixture()
    def conn(self):
        from datetime import datetime

        c = AsyncMock()
        c.fetchrow.side_effect = [
            # require_auth — entitlement lookup
            {"entitlement": "pro"},
            # INSERT RETURNING
            {
                "id": KEY_ID,
                "prefix": "sk-abc12",
                "label": "test-key",
                "entitlement": "pro",
                "last_used_at": None,
                "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                "revoked_at": None,
            },
        ]
        return c

    @pytest.fixture()
    def client(self, conn):
        app = _make_app(api_keys_router)

        @asynccontextmanager
        async def _scope(pool, workspace_id):
            yield conn

        with (
            patch("backend.routers.api_keys.workspace_scope", _scope),
            patch("backend.deps.workspace_scope", _scope),
        ):
            with TestClient(app) as c:
                yield c

    def test_create_returns_raw_key(self, client):
        resp = client.post(
            "/v1/api-keys",
            json={"label": "test-key", "entitlement": "pro"},
            headers=_auth_header(),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "raw_key" in body
        assert body["raw_key"].startswith("sk-")
        assert body["entitlement"] == "pro"
        assert body["is_revoked"] is False

    def test_entitlement_capped_at_workspace_level(self, conn, client):
        from datetime import datetime

        # Workspace is 'pro' but key requests 'pro_plus' — should be capped to 'pro'
        conn.fetchrow.side_effect = [
            {"entitlement": "pro"},
            {
                "id": KEY_ID,
                "prefix": "sk-abc12",
                "label": None,
                "entitlement": "pro",  # capped
                "last_used_at": None,
                "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                "revoked_at": None,
            },
        ]
        resp = client.post(
            "/v1/api-keys",
            json={"entitlement": "pro_plus"},
            headers=_auth_header(),
        )
        assert resp.status_code == 201

    def test_requires_auth(self, client):
        resp = client.post("/v1/api-keys", json={"entitlement": "pro"})
        assert resp.status_code == 401


# ── GET /v1/api-keys ──────────────────────────────────────────────────────────


class TestListApiKeys:
    @pytest.fixture()
    def conn(self):
        from datetime import datetime

        c = AsyncMock()
        c.fetchrow.return_value = {"entitlement": "pro"}
        c.fetch.return_value = [
            {
                "id": KEY_ID,
                "prefix": "sk-abc12",
                "label": "prod-key",
                "entitlement": "pro",
                "last_used_at": None,
                "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                "revoked_at": None,
            }
        ]
        return c

    @pytest.fixture()
    def client(self, conn):
        app = _make_app(api_keys_router)

        @asynccontextmanager
        async def _scope(pool, workspace_id):
            yield conn

        with (
            patch("backend.routers.api_keys.workspace_scope", _scope),
            patch("backend.deps.workspace_scope", _scope),
        ):
            with TestClient(app) as c:
                yield c

    def test_lists_keys(self, client):
        resp = client.get("/v1/api-keys", headers=_auth_header())
        assert resp.status_code == 200
        keys = resp.json()
        assert len(keys) == 1
        assert keys[0]["prefix"] == "sk-abc12"
        assert "raw_key" not in keys[0]

    def test_requires_auth(self, client):
        resp = client.get("/v1/api-keys")
        assert resp.status_code == 401


# ── DELETE /v1/api-keys/{key_id} ──────────────────────────────────────────────


class TestRevokeApiKey:
    @pytest.fixture()
    def conn(self):
        c = AsyncMock()
        c.execute.return_value = "UPDATE 1"
        c.fetchrow.side_effect = [
            {"entitlement": "pro"},  # require_auth
            {"revoked_at": None},  # key lookup — not yet revoked
        ]
        return c

    @pytest.fixture()
    def client(self, conn):
        app = _make_app(api_keys_router)

        @asynccontextmanager
        async def _scope(pool, workspace_id):
            yield conn

        with (
            patch("backend.routers.api_keys.workspace_scope", _scope),
            patch("backend.deps.workspace_scope", _scope),
        ):
            with TestClient(app) as c:
                yield c

    def test_revoke_existing_key(self, client):
        resp = client.delete(f"/v1/api-keys/{KEY_ID}", headers=_auth_header())
        assert resp.status_code == 200
        assert resp.json()["status"] == "revoked"

    def test_revoke_missing_key_returns_404(self, conn, client):
        conn.fetchrow.side_effect = [
            {"entitlement": "pro"},
            None,
        ]
        resp = client.delete(f"/v1/api-keys/{KEY_ID}", headers=_auth_header())
        assert resp.status_code == 404

    def test_revoke_already_revoked_returns_409(self, conn, client):
        from datetime import datetime

        conn.fetchrow.side_effect = [
            {"entitlement": "pro"},
            {"revoked_at": datetime(2025, 1, 1, tzinfo=UTC)},
        ]
        resp = client.delete(f"/v1/api-keys/{KEY_ID}", headers=_auth_header())
        assert resp.status_code == 409

    def test_requires_auth(self, client):
        resp = client.delete(f"/v1/api-keys/{KEY_ID}")
        assert resp.status_code == 401


# ── PATCH /v1/account ─────────────────────────────────────────────────────────


class TestPatchAccount:
    @pytest.fixture()
    def pool(self):
        p = MagicMock()
        conn = AsyncMock()
        conn.execute.return_value = "UPDATE 1"
        # entitlement for require_auth + password_hash for the password-change SELECT
        conn.fetchrow.return_value = {"entitlement": "pro", "password_hash": "bcrypt-hash"}
        p.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        p.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        return p, conn

    @pytest.fixture()
    def client(self, pool):
        p, conn = pool
        app = _make_app(account_router)
        app.state.pool = p

        @asynccontextmanager
        async def _scope(pool, workspace_id):
            yield conn

        with patch("backend.deps.workspace_scope", _scope):
            with TestClient(app) as c:
                yield c, conn

    def test_update_display_name(self, client):
        c, conn = client
        resp = c.patch(
            "/v1/account",
            json={"display_name": "New Name"},
            headers=_auth_header(),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    def test_update_password_requires_current_password(self, client):
        c, _ = client
        resp = c.patch(
            "/v1/account",
            json={"new_password": "newpass123"},
            headers=_auth_header(),
        )
        assert resp.status_code == 422

    def test_wrong_current_password_is_invalid_credentials(self, client):
        c, conn = client
        with patch("backend.auth.verify_password", return_value=False):
            resp = c.patch(
                "/v1/account",
                json={"new_password": "newpass123", "current_password": "wrong-one"},
                headers=_auth_header(),
            )
        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "invalid_credentials"
        assert "WWW-Authenticate" not in resp.headers
        assert not any("UPDATE users" in str(call) for call in conn.execute.call_args_list)

    def test_federated_user_cannot_set_a_first_password(self, client):
        """No password_hash (federated) → 409. A bearer token alone must not be able to
        plant a persistent password login on an IdP-only account."""
        c, conn = client
        conn.fetchrow.return_value = {"entitlement": "pro", "password_hash": None}
        resp = c.patch(
            "/v1/account",
            json={"new_password": "newpass123", "display_name": "Renamed"},
            headers=_auth_header(),
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == {"code": "no_password_credential"}
        assert not any("UPDATE users" in str(call) for call in conn.execute.call_args_list)

    def test_federated_user_can_still_update_display_name(self, client):
        c, conn = client
        conn.fetchrow.return_value = {"entitlement": "pro", "password_hash": None}
        resp = c.patch("/v1/account", json={"display_name": "Renamed"}, headers=_auth_header())
        assert resp.status_code == 200

    def test_no_fields_returns_422(self, client):
        c, _ = client
        resp = c.patch("/v1/account", json={}, headers=_auth_header())
        assert resp.status_code == 422

    def test_requires_auth(self, client):
        c, _ = client
        resp = c.patch("/v1/account", json={"display_name": "x"})
        assert resp.status_code == 401


# ── DELETE /v1/account ────────────────────────────────────────────────────────


class TestDeleteAccount:
    @pytest.fixture()
    def pool(self):
        p = MagicMock()
        conn = AsyncMock()
        conn.execute.return_value = "DELETE 1"
        # includes password_hash for the step-up re-auth SELECT + entitlement for require_auth
        conn.fetchrow.return_value = {"entitlement": "free", "password_hash": "bcrypt-hash"}
        p.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        p.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        return p, conn

    @pytest.fixture()
    def client(self, pool):
        p, conn = pool
        app = _make_app(account_router)
        app.state.pool = p
        # delete_account resolves the per-workspace tree via cfg.repo_root (P3); the
        # path does not exist in tests, so no rmtree runs.
        app.state.cfg = MagicMock(repo_root=Path("/nonexistent-gtm-test-root"))

        @asynccontextmanager
        async def _scope(pool, workspace_id):
            yield conn

        with (
            patch("backend.deps.workspace_scope", _scope),
            patch("backend.auth.verify_password", return_value=True),
        ):
            with TestClient(app) as c:
                yield c, conn

    def test_delete_account_success(self, client):
        c, _ = client
        resp = c.request(
            "DELETE", "/v1/account", headers=_auth_header(), json={"current_password": "pw"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_delete_user_not_found_returns_404(self, client):
        c, conn = client
        conn.execute.return_value = "DELETE 0"
        resp = c.request(
            "DELETE", "/v1/account", headers=_auth_header(), json={"current_password": "pw"}
        )
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        c, _ = client
        resp = c.request("DELETE", "/v1/account", json={"current_password": "pw"})
        assert resp.status_code == 401

    def test_password_user_must_send_current_password(self, client):
        """verify_password is patched True here, so only the handler's own check can
        refuse — a missing password, or an idp_token in its place, is a 401."""
        c, conn = client
        for body in ({}, {"idp_token": "header.payload.sig"}):
            resp = c.request("DELETE", "/v1/account", headers=_auth_header(), json=body)
            assert resp.status_code == 401, body
        assert not any("DELETE FROM users" in str(call) for call in conn.execute.call_args_list)

    def test_password_user_wrong_password_401(self, client):
        c, conn = client
        with patch("backend.auth.verify_password", return_value=False):
            resp = c.request(
                "DELETE", "/v1/account", headers=_auth_header(), json={"current_password": "no"}
            )
        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "invalid_credentials"
        assert "WWW-Authenticate" not in resp.headers
        assert not any("DELETE FROM users" in str(call) for call in conn.execute.call_args_list)


@pytest.fixture(scope="module")
def idp_key():
    from cryptography.hazmat.primitives.asymmetric import rsa

    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def idp_ec_key():
    from cryptography.hazmat.primitives.asymmetric import ec

    return ec.generate_private_key(ec.SECP256R1())


OTHER_USER_ID = "00000000-0000-0000-0000-000000000003"


class TestDeleteAccountFederated:
    """A federated user (password_hash NULL, V016) steps up with a FRESH IdP token,
    verified by the same backend/oidc.py path /v1/auth/exchange uses, whose
    (issuer, subject) must map to THIS user in external_identities."""

    @pytest.fixture()
    def client(self, monkeypatch, idp_key, idp_ec_key):
        from backend import oidc
        from tests.backend.test_auth_exchange import (
            ISSUER_A,
            ISSUER_B,
            JwksServer,
            _default_config,
            _jwk,
        )

        server = JwksServer()
        server.docs[f"{ISSUER_A}/jwks.json"] = {
            "keys": [_jwk(idp_key.public_key(), "kid-a1", "RS256")]
        }
        server.docs[f"{ISSUER_B}/jwks.json"] = {
            "keys": [_jwk(idp_ec_key.public_key(), "kid-b1", "ES256")]
        }
        # Both issuers trusted, so a same-subject token from ISSUER_B verifies and only the
        # (issuer, subject) link can refuse it.
        monkeypatch.setenv("TRUSTED_ISSUERS", _default_config(with_b=True))
        oidc.reset_state()
        monkeypatch.setattr(oidc, "_httpx_transport", server)

        p = MagicMock()
        conn = AsyncMock()
        conn.execute.return_value = "DELETE 1"
        conn.fetchrow.return_value = {"entitlement": "free", "password_hash": None}

        # external_identities as a table: (issuer, subject) -> user_id. "alice" at ISSUER_A
        # is THIS user; "bob" at ISSUER_A is a real, linked identity of ANOTHER user.
        identities = {(ISSUER_A, "alice"): USER_ID, (ISSUER_A, "bob"): OTHER_USER_ID}

        async def _fetchval(sql, *args):
            if "FROM external_identities" in sql:
                issuer, subject, user_id = args
                return 1 if identities.get((issuer, subject)) == user_id else None
            raise AssertionError(f"unexpected fetchval: {sql}")

        conn.fetchval.side_effect = _fetchval
        p.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        p.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        app = _make_app(account_router)
        register_error_handlers(app)  # assert the envelope the client actually reads
        app.state.pool = p
        app.state.cfg = MagicMock(repo_root=Path("/nonexistent-gtm-test-root"))

        @asynccontextmanager
        async def _scope(pool, workspace_id):
            yield conn

        with (
            patch("backend.deps.workspace_scope", _scope),
            # A password check that would PASS — proves current_password is never
            # accepted as a substitute for the IdP step-up.
            patch("backend.auth.verify_password", return_value=True),
        ):
            with TestClient(app) as c:
                yield c, conn
        oidc.reset_state()

    @staticmethod
    def _deleted(conn) -> bool:
        return any("DELETE FROM users" in str(call) for call in conn.execute.call_args_list)

    def test_matching_idp_token_deletes(self, client, idp_key):
        from tests.backend.test_auth_exchange import _token

        c, conn = client
        resp = c.request(
            "DELETE",
            "/v1/account",
            headers=_auth_header(),
            json={"idp_token": _token(idp_key, sub="alice")},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        assert self._deleted(conn)

    def test_valid_token_for_a_different_subject_401(self, client, idp_key):
        from tests.backend.test_auth_exchange import _token

        c, conn = client
        resp = c.request(
            "DELETE",
            "/v1/account",
            headers=_auth_header(),
            json={"idp_token": _token(idp_key, sub="mallory")},
        )
        assert resp.status_code == 401
        assert not self._deleted(conn)

    def _delete_with(self, c, token: str):
        return c.request("DELETE", "/v1/account", headers=_auth_header(), json={"idp_token": token})

    def test_token_linked_to_a_different_user_401(self, client, idp_key):
        """A valid token whose (issuer, subject) IS in external_identities — for someone else.

        This branch builds its own detail in account.py, separate from oidc.py's two, so the
        whole envelope is asserted here and at an oidc-raised refusal (the stale-token case
        below): require_federated_identity promises ONE indistinguishable 401, and a probe must
        not be able to tell "not your identity" from "token rejected"."""
        from tests.backend.test_auth_exchange import _token

        c, conn = client
        resp = self._delete_with(c, _token(idp_key, sub="bob"))
        assert resp.status_code == 401
        assert resp.json()["error"] == _UNIFORM_FEDERATED_401
        assert "WWW-Authenticate" not in resp.headers
        assert not self._deleted(conn)

    def test_same_subject_from_another_trusted_issuer_401(self, client, idp_ec_key):
        """Rule 4: the identity key is (issuer, subject). "alice" at ISSUER_B is not "alice"
        at ISSUER_A, even though both issuers are trusted and the token verifies."""
        from tests.backend.test_auth_exchange import ISSUER_B, _token

        c, conn = client
        token = _token(idp_ec_key, alg="ES256", kid="kid-b1", iss=ISSUER_B, sub="alice")
        resp = self._delete_with(c, token)
        assert resp.status_code == 401
        assert not self._deleted(conn)

    def test_expired_idp_token_401(self, client, idp_key):
        from tests.backend.test_auth_exchange import _token

        c, conn = client
        resp = self._delete_with(c, _token(idp_key, sub="alice", exp_delta=-3600))
        assert resp.status_code == 401
        assert not self._deleted(conn)

    def test_stale_idp_token_401(self, client, idp_key):
        """Unexpired but issued 10 minutes ago — e.g. the very token sent to /exchange at
        sign-in. The step-up needs one issued within STEP_UP_MAX_AGE_S (5 minutes)."""
        from tests.backend.test_auth_exchange import _token

        c, conn = client
        resp = self._delete_with(c, _token(idp_key, sub="alice", iat_delta=-600))
        assert resp.status_code == 401
        assert resp.json()["error"] == _UNIFORM_FEDERATED_401  # raised by oidc.py, not account.py
        assert "WWW-Authenticate" not in resp.headers
        assert not self._deleted(conn)

    def test_idp_token_just_past_the_window_401(self, client, idp_key):
        """Boundary: 370 s is past STEP_UP_MAX_AGE_S plus the clock-skew leeway (360 s), so
        a looser window would let this through — the 600 s case alone cannot tell."""
        from tests.backend.test_auth_exchange import _token

        c, conn = client
        resp = self._delete_with(c, _token(idp_key, sub="alice", iat_delta=-370))
        assert resp.status_code == 401
        assert not self._deleted(conn)

    def test_idp_token_without_iat_401(self, client, idp_key):
        """No iat means no provable freshness — refused, not assumed fresh."""
        from tests.backend.test_auth_exchange import _token

        c, conn = client
        resp = self._delete_with(c, _token(idp_key, sub="alice", drop_iat=True))
        assert resp.status_code == 401
        assert not self._deleted(conn)

    def test_idp_token_issued_within_the_window_deletes(self, client, idp_key):
        """Positive control for the freshness rule: 4 minutes old is inside the 5-minute window."""
        from tests.backend.test_auth_exchange import _token

        c, conn = client
        resp = self._delete_with(c, _token(idp_key, sub="alice", iat_delta=-240))
        assert resp.status_code == 200
        assert self._deleted(conn)

    def test_missing_idp_token_401_not_500(self, client):
        c, conn = client
        for body in ({}, {"current_password": "pw"}):
            resp = c.request("DELETE", "/v1/account", headers=_auth_header(), json=body)
            assert resp.status_code == 401, body
        assert not self._deleted(conn)

    def test_forged_idp_token_401(self, client):
        from cryptography.hazmat.primitives.asymmetric import rsa

        from tests.backend.test_auth_exchange import _token

        c, conn = client
        forger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        resp = c.request(
            "DELETE",
            "/v1/account",
            headers=_auth_header(),
            json={"idp_token": _token(forger, sub="alice")},
        )
        assert resp.status_code == 401
        assert not self._deleted(conn)

    def test_federation_not_configured_503(self, client, monkeypatch, idp_key):
        from backend import oidc
        from tests.backend.test_auth_exchange import _token

        c, conn = client
        monkeypatch.setenv("TRUSTED_ISSUERS", "")
        oidc.reset_state()
        resp = c.request(
            "DELETE",
            "/v1/account",
            headers=_auth_header(),
            json={"idp_token": _token(idp_key, sub="alice")},
        )
        assert resp.status_code == 503
        assert resp.json()["detail"] == {"code": "federation_not_configured"}
        assert not self._deleted(conn)


# ── PATCH /v1/workspace/cost-cap ──────────────────────────────────────────────


class TestPatchCostCap:
    @pytest.fixture()
    def conn(self):
        c = AsyncMock()
        c.execute.return_value = "UPDATE 1"
        c.fetchrow.return_value = {"entitlement": "pro"}
        return c

    @pytest.fixture()
    def client(self, conn):
        app = _make_app(workspaces_router)

        @asynccontextmanager
        async def _scope(pool, workspace_id):
            yield conn

        with (
            patch("backend.routers.workspaces.workspace_scope", _scope),
            patch("backend.deps.workspace_scope", _scope),
        ):
            with TestClient(app) as c:
                yield c

    def test_update_cost_cap(self, client):
        resp = client.patch(
            "/v1/workspace/cost-cap",
            json={"monthly_cost_cap_usd": 100.0},
            headers=_auth_header(),
        )
        assert resp.status_code == 200
        assert resp.json()["monthly_cost_cap_usd"] == 100.0

    def test_zero_cap_rejected(self, client):
        resp = client.patch(
            "/v1/workspace/cost-cap",
            json={"monthly_cost_cap_usd": 0.0},
            headers=_auth_header(),
        )
        assert resp.status_code == 422

    def test_negative_cap_rejected(self, client):
        resp = client.patch(
            "/v1/workspace/cost-cap",
            json={"monthly_cost_cap_usd": -10.0},
            headers=_auth_header(),
        )
        assert resp.status_code == 422

    def test_cap_above_500_rejected(self, client):
        resp = client.patch(
            "/v1/workspace/cost-cap",
            json={"monthly_cost_cap_usd": 501.0},
            headers=_auth_header(),
        )
        assert resp.status_code == 422

    def test_cap_at_max_boundary(self, client):
        resp = client.patch(
            "/v1/workspace/cost-cap",
            json={"monthly_cost_cap_usd": 500.0},
            headers=_auth_header(),
        )
        assert resp.status_code == 200

    def test_subscription_not_found_returns_404(self, conn, client):
        conn.execute.return_value = "UPDATE 0"
        resp = client.patch(
            "/v1/workspace/cost-cap",
            json={"monthly_cost_cap_usd": 50.0},
            headers=_auth_header(),
        )
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        resp = client.patch(
            "/v1/workspace/cost-cap",
            json={"monthly_cost_cap_usd": 50.0},
        )
        assert resp.status_code == 401

    def test_free_tier_cannot_set_cap(self, conn, client):
        # VULN-0002: a FREE workspace must not raise its own $0 cap. require_tier
        # denies FREE/NONE before the UPDATE ever runs.
        conn.fetchrow.return_value = {"entitlement": "free"}
        resp = client.patch(
            "/v1/workspace/cost-cap",
            json={"monthly_cost_cap_usd": 499.0},
            headers=_auth_header(),
        )
        assert resp.status_code == 403
        conn.execute.assert_not_called()
