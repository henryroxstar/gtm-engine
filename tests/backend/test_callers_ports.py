"""Fleet executor contract, PRD §2.1 "Callers" — v1 caller identity, verification, and
admission (backend/callers/*). No live DB: pool/conn are AsyncMock/MagicMock or small
in-memory fakes, following tests/backend/test_agents.py's ``AgentsDb`` style.

Covers the Task 2 spec: verifiers (JWT user, API-key service), the AgentsTableRegistry,
the fail-closed AND composition in ``admit_all``, the gate/pack-mode/agent-binding
helpers, the Refusal->HTTPException adapter, and audit.record_refusal's never-raises
contract. Also covers the two follow-on gaps closed after the initial review:
``verify_chain`` structurally enforcing the kind/``on_behalf_of`` allowlist, and the
``require_principal``/``authorize_run_read`` pieces a not-yet-dispatched REST task wires
into routes.

Convention: no pytest-asyncio in this repo (see tests/backend/test_agents.py's module
docstring) — async bodies run via ``asyncio.run(_go())`` inside an ordinary sync test.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

from backend import auth as backend_auth  # noqa: E402
from backend.callers.audit import record_refusal  # noqa: E402
from backend.callers.defaults import (  # noqa: E402
    AgentsTableRegistry,
    ApiKeyServiceVerifier,
    JwtUserVerifier,
)
from backend.callers.dependency import (  # noqa: E402
    admit_all,
    authorize_run_read,
    bind_agent,
    refusal_to_http,
    require_human,
    require_pack_mode,
    verify_chain,
)
from backend.callers.ports import Refusal  # noqa: E402
from backend.callers.principal import Principal, is_admitted_kind  # noqa: E402
from gtm_core.capabilities import Entitlement  # noqa: E402

WORKSPACE = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())
AGENT_ID = str(uuid.uuid4())
KEY_ID = str(uuid.uuid4())


def _make_jwt(*, workspace_id: str | None = WORKSPACE, user_id: str | None = USER_ID) -> str:
    return backend_auth.create_access_token(user_id, workspace_id)


class _PoolStub:
    """Minimal asyncpg.Pool-ish stub: .acquire() is an async-cm yielding conn."""

    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _AcquireCtx(self._conn)


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        return False


@pytest.fixture(autouse=True)
def scoped_workspaces():
    """require_principal admits a bound key inside workspace_scope (agents is FORCE RLS).
    The mocked conn cannot run SET LOCAL, so this stands in for the scope — and records
    the workspace each scope was opened for, so a regression to a bare pool.acquire()
    (which refuses every bound key under RLS) fails here too, not only in the live tier."""
    opened: list[str] = []

    @asynccontextmanager
    async def _scope(pool, workspace_id):
        opened.append(workspace_id)
        async with pool.acquire() as conn:
            yield conn

    with patch("backend.callers.rest.workspace_scope", _scope):
        yield opened


def _service_principal(agent_id: str | None = AGENT_ID) -> Principal:
    return Principal(
        kind="service",
        subject=KEY_ID,
        workspace_id=WORKSPACE,
        entitlement=Entitlement.PRO,
        agent_id=agent_id,
        credential_id=KEY_ID,
        verifier="api_key",
    )


# ── Principal / kind allowlist ──────────────────────────────────────────────


def test_is_admitted_kind_allowlist():
    assert is_admitted_kind("user")
    assert is_admitted_kind("service")
    assert not is_admitted_kind("delegated_agent")
    assert not is_admitted_kind("anything_else_a_rule_never_named")


def test_on_behalf_of_cannot_be_set_nonnone():
    # Type-level guarantee: no code path in this module ever sets it. Constructing a
    # Principal without it defaults to None, and there is no keyword-independent way to
    # smuggle a value through the helpers under test (bind_agent/verify_chain/etc. never
    # read or write it).
    p = Principal(kind="user", subject=USER_ID, workspace_id=WORKSPACE, entitlement=Entitlement.PRO)
    assert p.on_behalf_of is None


# ── JwtUserVerifier ──────────────────────────────────────────────────────────


def test_jwt_verifier_success():
    async def _go():
        with patch(
            "backend.callers.defaults.fetch_entitlement",
            new=AsyncMock(return_value=Entitlement.PRO),
        ):
            token = _make_jwt()
            return await JwtUserVerifier().verify(token, pool=object())

    principal = asyncio.run(_go())
    assert principal.kind == "user"
    assert principal.subject == USER_ID
    assert principal.workspace_id == WORKSPACE
    assert principal.entitlement == Entitlement.PRO
    assert principal.verifier == "jwt"
    assert principal.on_behalf_of is None


def test_jwt_verifier_expired_token_refuses():
    # decode fails before fetch_entitlement is ever reached, so the pool is never touched.
    pool = object()
    expired = jwt.encode(
        {
            "sub": USER_ID,
            "workspace_id": WORKSPACE,
            "type": "access",
            "iat": 0,
            "exp": 1,  # 1970 — long expired, well past the leeway
        },
        os.environ["BACKEND_JWT_SECRET"],
        algorithm="HS256",
    )
    with pytest.raises(Refusal) as exc_info:
        asyncio.run(JwtUserVerifier().verify(expired, pool))
    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "token_expired"


def test_jwt_verifier_garbage_token_refuses_invalid():
    pool = object()  # decode fails first; fetch_entitlement is never reached
    with pytest.raises(Refusal) as exc_info:
        asyncio.run(JwtUserVerifier().verify("not-a-jwt-at-all", pool))
    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "token_invalid"


# ── ApiKeyServiceVerifier ────────────────────────────────────────────────────


def _pool_for_resolve_api_key(row):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=row)
    return _PoolStub(conn), conn


def test_api_key_verifier_unknown_key_refuses_401_invalid():
    pool, _conn = _pool_for_resolve_api_key(None)
    with pytest.raises(Refusal) as exc_info:
        asyncio.run(ApiKeyServiceVerifier().verify("sk-doesnotexist", pool))
    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "api_key_invalid"


def test_api_key_verifier_non_sk_prefix_refuses_401_invalid_without_db_call():
    pool, conn = _pool_for_resolve_api_key(None)
    with pytest.raises(Refusal) as exc_info:
        asyncio.run(ApiKeyServiceVerifier().verify("not-a-key", pool))
    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "api_key_invalid"
    conn.fetchrow.assert_not_called()


def test_api_key_verifier_unbound_key_refuses_403_and_workspace_is_known():
    # Distinguishing case: unlike the 401 (no row at all -> no workspace ever resolved),
    # this refusal happens AFTER a real row (and therefore a real workspace_id) was found.
    row = {
        "key_id": KEY_ID,
        "workspace_id": WORKSPACE,
        "entitlement": "pro",
        "agent_id": None,
    }
    pool, conn = _pool_for_resolve_api_key(row)
    with pytest.raises(Refusal) as exc_info:
        asyncio.run(ApiKeyServiceVerifier().verify("sk-unbound", pool))
    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "api_key_unbound"
    # the row (and its workspace) WAS resolved before the refusal — one DB round trip
    conn.fetchrow.assert_called_once()


def test_api_key_verifier_bound_key_builds_service_principal_with_capped_entitlement():
    row = {
        "key_id": KEY_ID,
        "workspace_id": WORKSPACE,
        "entitlement": "pro_plus",  # the key was minted at pro_plus...
        "agent_id": AGENT_ID,
    }
    pool, conn = _pool_for_resolve_api_key(row)

    async def _go():
        with patch(
            "backend.callers.defaults.fetch_entitlement",
            new=AsyncMock(return_value=Entitlement.PRO),
        ):
            # ...but the LIVE workspace entitlement (pro) is lower, so it wins (min-of-both).
            return await ApiKeyServiceVerifier().verify("sk-bound", pool)

    principal = asyncio.run(_go())
    assert principal.kind == "service"
    assert principal.subject == KEY_ID
    assert principal.workspace_id == WORKSPACE
    assert principal.entitlement == Entitlement.PRO
    assert principal.agent_id == AGENT_ID
    assert principal.credential_id == KEY_ID
    assert principal.verifier == "api_key"
    # evidence_digest is a 12-hex-char prefix of the SHA-256 hash, NEVER the raw key
    assert principal.evidence_digest is not None
    assert len(principal.evidence_digest) == 12
    assert principal.evidence_digest != "sk-bound"
    conn.fetchrow.assert_called_once()


def test_api_key_verifier_db_error_propagates_to_its_caller():
    # The verifier itself doesn't need to catch-and-wrap (unlike admit_all for
    # registries) — verify_chain is what turns this into a Refusal (see
    # test_verify_chain_wraps_a_verifier_that_raises_into_a_refusal below), the same
    # division of labour admit_all already has with each AgentRegistry.
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=RuntimeError("db exploded"))
    pool = _PoolStub(conn)
    with pytest.raises(RuntimeError):
        asyncio.run(ApiKeyServiceVerifier().verify("sk-whatever", pool))


# ── AgentsTableRegistry ──────────────────────────────────────────────────────


def test_agents_table_registry_noop_for_user_principal():
    conn = AsyncMock()
    conn.fetchrow = AsyncMock()
    user_principal = Principal(
        kind="user", subject=USER_ID, workspace_id=WORKSPACE, entitlement=Entitlement.PRO
    )
    asyncio.run(AgentsTableRegistry().admit(user_principal, conn))
    conn.fetchrow.assert_not_called()


def test_agents_table_registry_noop_for_service_without_agent_id():
    conn = AsyncMock()
    conn.fetchrow = AsyncMock()
    asyncio.run(AgentsTableRegistry().admit(_service_principal(agent_id=None), conn))
    conn.fetchrow.assert_not_called()


def test_agents_table_registry_paused_refuses_409():
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"status": "paused"})
    with pytest.raises(Refusal) as exc_info:
        asyncio.run(AgentsTableRegistry().admit(_service_principal(), conn))
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "agent_paused"


def test_agents_table_registry_archived_refuses_409():
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"status": "archived"})
    with pytest.raises(Refusal) as exc_info:
        asyncio.run(AgentsTableRegistry().admit(_service_principal(), conn))
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "agent_archived"


def test_agents_table_registry_any_non_active_non_paused_status_is_archived():
    # mirrors resolve_acting_agent: "anything not 'active'" (other than 'paused') -> archived
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"status": "some_future_status"})
    with pytest.raises(Refusal) as exc_info:
        asyncio.run(AgentsTableRegistry().admit(_service_principal(), conn))
    assert exc_info.value.code == "agent_archived"


def test_agents_table_registry_missing_row_matches_resolve_acting_agent_404():
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    with pytest.raises(Refusal) as exc_info:
        asyncio.run(AgentsTableRegistry().admit(_service_principal(), conn))
    assert exc_info.value.status_code == 404
    # A snake_case code, with admission.py's literal as the message — the same code the
    # envelope derives from "Unknown agent" on the A4 path.
    assert exc_info.value.code == "agent_not_found"
    assert exc_info.value.message == "Unknown agent"


def test_agents_table_registry_active_admits_silently():
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"status": "active"})
    result = asyncio.run(AgentsTableRegistry().admit(_service_principal(), conn))
    assert result is None  # allow == no exception, not a truthy return value


# ── admit_all: fail-closed AND composition ──────────────────────────────────


class _AllowRegistry:
    def __init__(self):
        self.called = False

    async def admit(self, principal, conn):
        self.called = True
        return None


class _RefuseRegistry:
    def __init__(self, code="denied", status_code=403):
        self.code = code
        self.status_code = status_code
        self.called = False

    async def admit(self, principal, conn):
        self.called = True
        raise Refusal(self.status_code, self.code)


class _RaisingRegistry:
    async def admit(self, principal, conn):
        raise RuntimeError("registry backend is down")


class _SlowRegistry:
    async def admit(self, principal, conn):
        await asyncio.sleep(10)


def test_admit_all_allows_when_every_registry_allows():
    conn = AsyncMock()
    principal = _service_principal()
    registries = [_AllowRegistry(), _AllowRegistry()]
    asyncio.run(admit_all(principal, registries, conn))
    assert all(r.called for r in registries)


def test_admit_all_refuses_on_a_registry_refusal_never_returns_200_shaped_result():
    conn = AsyncMock()
    principal = _service_principal()
    with pytest.raises(Refusal) as exc_info:
        asyncio.run(
            admit_all(principal, [_RefuseRegistry(code="agent_paused", status_code=409)], conn)
        )
    assert exc_info.value.code == "agent_paused"


def test_admit_all_wraps_unexpected_registry_exception_as_refusal_and_logs(caplog):
    conn = AsyncMock()
    principal = _service_principal()
    with caplog.at_level("WARNING"):
        with pytest.raises(Refusal) as exc_info:
            asyncio.run(admit_all(principal, [_RaisingRegistry()], conn))
    assert exc_info.value.status_code == 500
    assert exc_info.value.code == "registry_error"
    assert any("registry" in rec.message.lower() for rec in caplog.records)


def test_admit_all_timeout_refuses_fail_closed():
    conn = AsyncMock()
    principal = _service_principal()
    with pytest.raises(Refusal) as exc_info:
        asyncio.run(admit_all(principal, [_SlowRegistry()], conn, timeout_s=0.05))
    assert exc_info.value.code == "registry_timeout"
    assert exc_info.value.status_code in (503,)


def test_admit_all_first_refusal_short_circuits_a_would_be_allow_second_registry():
    # The "optional adapter's allow cannot override a local deny" case. admit_all's own
    # documented behavior is short-circuit-on-first-refusal (see the comment on admit_all),
    # so the second (would-be-allow) registry must never even be called.
    conn = AsyncMock()
    principal = _service_principal()
    first = _RefuseRegistry(code="agent_paused", status_code=409)
    second = _AllowRegistry()
    with pytest.raises(Refusal) as exc_info:
        asyncio.run(admit_all(principal, [first, second], conn))
    assert exc_info.value.code == "agent_paused"
    assert first.called
    assert not second.called  # never reached — the refusal already decided the outcome


# ── require_human: allowlist, not denylist ──────────────────────────────────


def test_require_human_admits_user():
    principal = Principal(
        kind="user", subject=USER_ID, workspace_id=WORKSPACE, entitlement=Entitlement.PRO
    )
    require_human(principal)  # does not raise


def test_require_human_refuses_service():
    principal = _service_principal()
    with pytest.raises(Refusal) as exc_info:
        require_human(principal)
    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "human_approval_required"


def test_require_human_refuses_delegated_agent_kind_never_seen_by_any_rule():
    # Constructed directly (a verifier would refuse this kind before it ever got here) to
    # prove the allowlist shape holds even for a kind no rule has ever admitted.
    principal = Principal(
        kind="delegated_agent",
        subject="future-agent-1",
        workspace_id=WORKSPACE,
        entitlement=Entitlement.FREE,
    )
    with pytest.raises(Refusal) as exc_info:
        require_human(principal)
    assert exc_info.value.code == "human_approval_required"
    # and it is inadmissible in general, not just at this one gate
    assert not is_admitted_kind(principal.kind)


# ── require_pack_mode ────────────────────────────────────────────────────────


def test_require_pack_mode_service_in_prompt_mode_refuses():
    principal = _service_principal()
    with pytest.raises(Refusal) as exc_info:
        require_pack_mode(principal, is_pack_mode=False)
    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "prompt_mode_requires_user"


def test_require_pack_mode_service_in_pack_mode_ok():
    principal = _service_principal()
    require_pack_mode(principal, is_pack_mode=True)  # does not raise


def test_require_pack_mode_user_in_prompt_mode_ok():
    principal = Principal(
        kind="user", subject=USER_ID, workspace_id=WORKSPACE, entitlement=Entitlement.PRO
    )
    require_pack_mode(principal, is_pack_mode=False)  # does not raise


def test_require_pack_mode_user_in_pack_mode_ok():
    principal = Principal(
        kind="user", subject=USER_ID, workspace_id=WORKSPACE, entitlement=Entitlement.PRO
    )
    require_pack_mode(principal, is_pack_mode=True)  # does not raise


# ── bind_agent ────────────────────────────────────────────────────────────────


def test_bind_agent_user_passthrough_requested_id():
    principal = Principal(
        kind="user", subject=USER_ID, workspace_id=WORKSPACE, entitlement=Entitlement.PRO
    )
    assert bind_agent(principal, "some-other-agent") == "some-other-agent"


def test_bind_agent_user_passthrough_none():
    principal = Principal(
        kind="user", subject=USER_ID, workspace_id=WORKSPACE, entitlement=Entitlement.PRO
    )
    assert bind_agent(principal, None) is None


def test_bind_agent_service_forces_its_own_agent_id_when_none_requested():
    principal = _service_principal(agent_id=AGENT_ID)
    assert bind_agent(principal, None) == AGENT_ID


def test_bind_agent_service_matching_requested_id_ok():
    principal = _service_principal(agent_id=AGENT_ID)
    assert bind_agent(principal, AGENT_ID) == AGENT_ID


def test_bind_agent_service_mismatched_requested_id_refuses():
    principal = _service_principal(agent_id=AGENT_ID)
    with pytest.raises(Refusal) as exc_info:
        bind_agent(principal, "some-other-agent-entirely")
    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "agent_mismatch"


# ── verify_chain: fixed prefix dispatch, never trial-and-error ──────────────


def test_verify_chain_routes_sk_prefixed_token_only_to_service_verifier():
    user_verifier = AsyncMock()
    service_verifier = AsyncMock()
    expected = _service_principal()
    service_verifier.verify = AsyncMock(return_value=expected)
    pool = object()
    result = asyncio.run(verify_chain("sk-abc123", pool, [user_verifier, service_verifier]))
    assert result is expected
    service_verifier.verify.assert_called_once_with("sk-abc123", pool)
    user_verifier.verify.assert_not_called()


def test_verify_chain_routes_non_sk_token_only_to_user_verifier():
    user_verifier = AsyncMock()
    service_verifier = AsyncMock()
    expected = Principal(
        kind="user", subject=USER_ID, workspace_id=WORKSPACE, entitlement=Entitlement.PRO
    )
    user_verifier.verify = AsyncMock(return_value=expected)
    pool = object()
    result = asyncio.run(verify_chain("some.jwt.token", pool, [user_verifier, service_verifier]))
    assert result is expected
    user_verifier.verify.assert_called_once_with("some.jwt.token", pool)
    service_verifier.verify.assert_not_called()


def test_verify_chain_admits_ordinary_user_and_service_principals_unchanged():
    # Regression guard for the structural check below: today's two admitted kinds must
    # still pass straight through verify_chain.
    user_verifier = AsyncMock()
    user_principal = Principal(
        kind="user", subject=USER_ID, workspace_id=WORKSPACE, entitlement=Entitlement.PRO
    )
    user_verifier.verify = AsyncMock(return_value=user_principal)
    service_verifier = AsyncMock()
    service_principal = _service_principal()
    service_verifier.verify = AsyncMock(return_value=service_principal)
    pool = object()
    assert asyncio.run(verify_chain("some.jwt.token", pool, [user_verifier, service_verifier])) is (
        user_principal
    )
    assert asyncio.run(verify_chain("sk-abc123", pool, [user_verifier, service_verifier])) is (
        service_principal
    )


def test_verify_chain_refuses_a_kind_not_in_v1_admitted_kinds_structurally():
    # A stub verifier returns a "delegated_agent" Principal DIRECTLY, bypassing any real
    # verifier's own logic entirely. This proves the enforcement lives inside verify_chain
    # itself — not merely that no shipped verifier happens to produce one today.
    bad_principal = Principal(
        kind="delegated_agent",
        subject="future-agent-1",
        workspace_id=WORKSPACE,
        entitlement=Entitlement.FREE,
    )
    user_verifier = AsyncMock()
    user_verifier.verify = AsyncMock(return_value=bad_principal)
    service_verifier = AsyncMock()
    pool = object()
    with pytest.raises(Refusal) as exc_info:
        asyncio.run(verify_chain("not-a-service-key", pool, [user_verifier, service_verifier]))
    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "delegation_not_supported"
    assert exc_info.value.principal is bad_principal


def test_verify_chain_refuses_a_principal_with_non_none_on_behalf_of():
    # Principal declares on_behalf_of "always None in v1; no setter exists" — construct the
    # violation via dataclasses.replace to simulate a future/misbehaving verifier that sets
    # it anyway, and prove verify_chain refuses it even though the kind itself IS admitted.
    base = _service_principal()
    bad_principal = dataclasses.replace(base, on_behalf_of="someone-else")
    service_verifier = AsyncMock()
    service_verifier.verify = AsyncMock(return_value=bad_principal)
    user_verifier = AsyncMock()
    pool = object()
    with pytest.raises(Refusal) as exc_info:
        asyncio.run(verify_chain("sk-whatever", pool, [user_verifier, service_verifier]))
    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "delegation_not_supported"


def test_verify_chain_wraps_a_verifier_that_raises_into_a_refusal():
    # PRD §2.1 rule 3, fail closed: a verifier raising an unexpected exception (a DB
    # blip, per test_api_key_verifier_db_error_propagates_to_its_caller above) must not
    # leak past verify_chain as a bare, unaudited exception — mirrors admit_all's
    # existing except Exception -> Refusal(500, "registry_error") treatment of a
    # registry that raises.
    service_verifier = AsyncMock()
    service_verifier.verify = AsyncMock(side_effect=RuntimeError("db exploded"))
    user_verifier = AsyncMock()
    pool = object()
    with pytest.raises(Refusal) as exc_info:
        asyncio.run(verify_chain("sk-whatever", pool, [user_verifier, service_verifier]))
    assert exc_info.value.status_code == 500
    assert exc_info.value.code == "verifier_error"
    # No Principal was ever established, so this refusal is unattributable to any
    # workspace — require_principal's caller routes this to the structured log, never
    # denials.jsonl (see test_require_principal_unattributable_refusal_never_writes_denials_ledger).
    assert exc_info.value.principal is None


def test_verify_chain_wraps_a_verifier_that_times_out_into_a_503_refusal():
    async def _hangs(raw_token, pool):
        await asyncio.sleep(10)

    service_verifier = AsyncMock()
    service_verifier.verify = _hangs
    user_verifier = AsyncMock()
    pool = object()
    with pytest.raises(Refusal) as exc_info:
        asyncio.run(
            verify_chain("sk-whatever", pool, [user_verifier, service_verifier], timeout_s=0.01)
        )
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "verifier_timeout"


# ── authorize_run_read: own-run / workspace scoping for read routes ─────────


def test_authorize_run_read_user_always_passes_regardless_of_run_agent_id():
    principal = Principal(
        kind="user", subject=USER_ID, workspace_id=WORKSPACE, entitlement=Entitlement.PRO
    )
    # A clearly-mismatched agent_id must still pass for a user principal — zero extra work.
    assert authorize_run_read(principal, "some-clearly-mismatched-agent-id") is None
    assert authorize_run_read(principal, None) is None
    assert authorize_run_read(principal, AGENT_ID, read_scope="own") is None


def test_authorize_run_read_service_matching_agent_id_passes():
    principal = _service_principal(agent_id=AGENT_ID)
    assert authorize_run_read(principal, AGENT_ID) is None
    assert authorize_run_read(principal, AGENT_ID, read_scope="own") is None


def test_authorize_run_read_service_non_matching_own_scope_refuses_404_not_403():
    # PRD §2.1 rule 3 / G0: a mismatch is a 404, never a 403 — existence must not leak.
    principal = _service_principal(agent_id=AGENT_ID)
    with pytest.raises(Refusal) as exc_info:
        authorize_run_read(principal, "some-other-agent-entirely", read_scope="own")
    assert exc_info.value.status_code == 404


def test_authorize_run_read_service_non_matching_workspace_scope_passes():
    # A "workspace" read_scope is the coordinator-agent case (G0): sees every run in its
    # workspace regardless of which agent it belongs to.
    principal = _service_principal(agent_id=AGENT_ID)
    assert (
        authorize_run_read(principal, "some-other-agent-entirely", read_scope="workspace") is None
    )


# ── refusal_to_http adapter ──────────────────────────────────────────────────


def test_refusal_to_http_with_message():
    refusal = Refusal(401, "token_expired", "Access token expired")
    http_exc = refusal_to_http(refusal)
    assert http_exc.status_code == 401
    assert http_exc.detail == {"code": "token_expired", "message": "Access token expired"}


def test_refusal_to_http_without_message():
    refusal = Refusal(409, "agent_paused")
    http_exc = refusal_to_http(refusal)
    assert http_exc.status_code == 409
    assert http_exc.detail == {"code": "agent_paused"}


# ── audit.record_refusal: never raises ──────────────────────────────────────


def test_record_refusal_never_raises_even_when_the_ledger_append_fails():
    # record_refusal owns its own best-effort guard: a ledger that cannot even be
    # constructed must not turn an admission refusal into an unrelated 500.
    principal = _service_principal()
    with patch("gtm_core.ledgers.Ledgers", side_effect=RuntimeError("ledger append exploded")):
        try:
            record_refusal(
                cfg=object(),
                profile="acme",
                principal=principal,
                route="/v1/runs",
                code="agent_paused",
            )
        except RuntimeError:
            pytest.fail("record_refusal must never raise")


def _written_denials(tmp_path, profile: str = "acme") -> list[dict]:
    import json

    path = tmp_path / "content" / profile / "denials.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_record_refusal_writes_a_row_naming_the_principal_and_code(tmp_path):
    # Reads the REAL file: a sink that dropped the principal would still have been
    # "called with" it, so asserting on call arguments cannot catch that.
    record_refusal(
        cfg=_minimal_cfg(tmp_path),
        profile="acme",
        principal=_service_principal(),
        route="/v1/runs",
        code="agent_paused",
    )
    (row,) = _written_denials(tmp_path)
    assert row["tool"] == "REST:/v1/runs"
    assert row["decision"] == "deny"
    assert row["outcome"] == "denied"
    assert row["source"] == "backend-admission"
    assert row["principal_kind"] == "service"
    assert row["principal_id"] == KEY_ID
    assert row["code"] == "agent_paused"
    assert "sk-" not in json.dumps(row)


def test_record_refusal_with_no_principal_degrades_to_none_fields(tmp_path):
    record_refusal(
        cfg=_minimal_cfg(tmp_path),
        profile="acme",
        principal=None,
        route="/v1/runs",
        code="token_invalid",
    )
    (row,) = _written_denials(tmp_path)
    assert row["principal_kind"] is None
    assert row["principal_id"] is None
    assert row["code"] == "token_invalid"


# ── require_principal: the Depends() a REST route will use ─────────────────
#
# A later, not-yet-dispatched task wires this into backend/deps.py-style routes. These
# tests drive it directly with a minimal fake Request (matching backend/deps.py's own
# request.app.state.pool access pattern) rather than a real FastAPI app/TestClient.


class _FakeAppState:
    def __init__(self, pool, cfg):
        self.pool = pool
        self.cfg = cfg


class _FakeApp:
    def __init__(self, pool, cfg):
        self.state = _FakeAppState(pool, cfg)


class _FakeRequest:
    def __init__(self, pool, cfg, path="/v1/runs"):
        self.app = _FakeApp(pool, cfg)
        self.state = SimpleNamespace()
        self.url = SimpleNamespace(path=path)


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_require_principal_valid_user_jwt_admits_and_sets_request_state():
    from backend.callers.rest import require_principal

    async def _go():
        with patch(
            "backend.callers.defaults.fetch_entitlement",
            new=AsyncMock(return_value=Entitlement.PRO),
        ):
            request = _FakeRequest(pool=object(), cfg=object())
            principal = await require_principal(request, _creds(_make_jwt()))
            return principal, request

    principal, request = asyncio.run(_go())
    assert principal.kind == "user"
    assert principal.subject == USER_ID
    assert request.state.principal is principal


def test_require_principal_valid_active_agent_api_key_admits(scoped_workspaces):
    from backend.callers.rest import require_principal

    row = {
        "key_id": KEY_ID,
        "workspace_id": WORKSPACE,
        "entitlement": "pro",
        "agent_id": AGENT_ID,
    }
    conn = AsyncMock()
    # First fetchrow is ApiKeyServiceVerifier's resolve_api_key lookup; second is
    # AgentsTableRegistry's own admission query.
    conn.fetchrow = AsyncMock(side_effect=[row, {"status": "active"}])
    pool = _PoolStub(conn)

    async def _go():
        with patch(
            "backend.callers.defaults.fetch_entitlement",
            new=AsyncMock(return_value=Entitlement.PRO),
        ):
            request = _FakeRequest(pool=pool, cfg=object())
            principal = await require_principal(request, _creds("sk-good"))
            return principal, request

    principal, request = asyncio.run(_go())
    assert principal.kind == "service"
    assert principal.agent_id == AGENT_ID
    assert request.state.principal is principal
    assert conn.fetchrow.call_count == 2
    # The agent admission ran inside the principal's own workspace scope.
    assert scoped_workspaces == [WORKSPACE]


def _minimal_cfg(tmp_path):
    """A cheap, real Config instance — required now that the Refusal branch calls
    _workspace_scoped_config, which does dataclasses.replace(cfg, ...) and therefore
    needs a real Config dataclass, not object()."""
    from agent.config import Config

    return Config(
        repo_root=tmp_path,
        plugin_path=tmp_path / "plugin",
        profiles_root=tmp_path / "profiles",
        content_root=tmp_path / "content",
        default_profile="acme",
    )


def test_require_principal_paused_agent_refuses_and_records_denial(tmp_path):
    from backend.callers.rest import require_principal

    row = {"key_id": KEY_ID, "workspace_id": WORKSPACE, "entitlement": "pro", "agent_id": AGENT_ID}
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=[row, {"status": "paused"}])
    pool = _PoolStub(conn)
    cfg = _minimal_cfg(tmp_path)

    async def _go():
        with (
            patch(
                "backend.callers.defaults.fetch_entitlement",
                new=AsyncMock(return_value=Entitlement.PRO),
            ),
            patch("backend.callers.rest.audit.record_refusal") as mock_record,
        ):
            request = _FakeRequest(pool=pool, cfg=cfg)
            with pytest.raises(HTTPException) as exc_info:
                await require_principal(request, _creds("sk-paused"))
            return exc_info, mock_record

    exc_info, mock_record = asyncio.run(_go())
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "agent_paused"
    mock_record.assert_called_once()
    refused_principal = mock_record.call_args.args[2]
    assert refused_principal is not None
    assert refused_principal.kind == "service"
    # Bug 1 fix: the config passed to record_refusal must be workspace-scoped, never the
    # global app.state.cfg — proven by object identity AND by the scoped content_root.
    scoped_cfg = mock_record.call_args.args[0]
    assert scoped_cfg is not cfg
    assert scoped_cfg.content_root != cfg.content_root
    assert WORKSPACE in str(scoped_cfg.content_root)


def test_require_principal_invalid_api_key_refuses_401(tmp_path):
    from backend.callers.rest import require_principal

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    pool = _PoolStub(conn)
    cfg = _minimal_cfg(tmp_path)

    async def _go():
        with (
            patch("backend.callers.rest.audit.record_refusal") as mock_record,
            patch("backend.callers.rest.logger.warning") as mock_warning,
        ):
            request = _FakeRequest(pool=pool, cfg=cfg)
            with pytest.raises(HTTPException) as exc_info:
                await require_principal(request, _creds("sk-doesnotexist"))
            return exc_info, mock_record, mock_warning

    exc_info, mock_record, mock_warning = asyncio.run(_go())
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == "api_key_invalid"
    # Bug 2 fix: an unattributable refusal (no workspace ever resolved) must NEVER write to
    # denials.jsonl — it goes to the structured backend log instead.
    mock_record.assert_not_called()
    mock_warning.assert_called_once()


def test_require_principal_refuses_delegated_agent_kind_end_to_end(tmp_path):
    # Not a unit test of verify_chain in isolation: proves the Gap-2 structural check also
    # protects the actual require_principal call path a route will Depends() on.
    from backend.callers.rest import require_principal

    bad_principal = Principal(
        kind="delegated_agent",
        subject="future-agent-1",
        workspace_id=WORKSPACE,
        entitlement=Entitlement.FREE,
    )
    cfg = _minimal_cfg(tmp_path)

    async def _go():
        with (
            patch.object(JwtUserVerifier, "verify", new=AsyncMock(return_value=bad_principal)),
            patch("backend.callers.rest.audit.record_refusal") as mock_record,
        ):
            request = _FakeRequest(pool=object(), cfg=cfg)
            with pytest.raises(HTTPException) as exc_info:
                await require_principal(request, _creds("not-a-service-key"))
            return exc_info, mock_record

    exc_info, mock_record = asyncio.run(_go())
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "delegation_not_supported"
    mock_record.assert_called_once()
    # this refusal DOES carry a known workspace_id (bad_principal.workspace_id == WORKSPACE),
    # so it goes through the workspace-scoped config path, not the global one.
    scoped_cfg = mock_record.call_args.args[0]
    assert scoped_cfg is not cfg


def test_require_principal_verifier_error_refuses_500_and_logs_not_denials():
    # A verifier raising (a DB blip, per test_verify_chain_wraps_a_verifier_that_raises_into_a_refusal)
    # never established a Principal, so it's unattributable exactly like an unknown/revoked
    # key — proves the two failure modes converge on the same safe, audited-by-log path
    # through the actual require_principal() a route Depends() on, not just at verify_chain.
    from backend.callers.rest import require_principal

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=RuntimeError("db exploded"))
    pool = _PoolStub(conn)

    async def _go():
        with patch("backend.callers.rest.audit.record_refusal") as mock_record:
            request = _FakeRequest(pool=pool, cfg=object())
            with pytest.raises(HTTPException) as exc_info:
                await require_principal(request, _creds("sk-whatever"))
            return exc_info, mock_record

    exc_info, mock_record = asyncio.run(_go())
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail["code"] == "verifier_error"
    mock_record.assert_not_called()


def test_require_principal_unattributable_refusal_never_writes_denials_ledger():
    # Bug 2, dedicated: a key that resolves NO workspace at all (unknown/revoked/expired) is
    # genuinely unattributable — there is no per-workspace denials.jsonl to write it into, so
    # record_refusal must never be called; a structured backend log line stands in instead.
    from backend.callers.rest import require_principal

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    pool = _PoolStub(conn)

    async def _go():
        with (
            patch("backend.callers.rest.audit.record_refusal") as mock_record,
            patch("backend.callers.rest.logger.warning") as mock_warning,
        ):
            request = _FakeRequest(pool=pool, cfg=object())
            with pytest.raises(HTTPException):
                await require_principal(request, _creds("sk-unknown-entirely"))
            return mock_record, mock_warning

    mock_record, mock_warning = asyncio.run(_go())
    mock_record.assert_not_called()
    mock_warning.assert_called_once()
    # never logs the raw credential
    logged = " ".join(str(a) for a in mock_warning.call_args.args)
    assert "sk-unknown-entirely" not in logged


def test_require_principal_known_principal_refusal_calls_workspace_scoped_config(tmp_path):
    # Bug 1, dedicated: prove the exact helper is invoked with the refused principal's
    # workspace_id, rather than inferring it indirectly through record_refusal's args.
    from backend.callers.rest import require_principal

    row = {"key_id": KEY_ID, "workspace_id": WORKSPACE, "entitlement": "pro", "agent_id": AGENT_ID}
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=[row, {"status": "archived"}])
    pool = _PoolStub(conn)
    cfg = _minimal_cfg(tmp_path)
    sentinel_scoped_cfg = object()

    async def _go():
        with (
            patch(
                "backend.callers.defaults.fetch_entitlement",
                new=AsyncMock(return_value=Entitlement.PRO),
            ),
            patch(
                "backend.callers.rest._workspace_scoped_config",
                return_value=sentinel_scoped_cfg,
            ) as mock_scope,
            patch("backend.callers.rest.audit.record_refusal") as mock_record,
        ):
            request = _FakeRequest(pool=pool, cfg=cfg)
            with pytest.raises(HTTPException):
                await require_principal(request, _creds("sk-archived"))
            return mock_scope, mock_record

    mock_scope, mock_record = asyncio.run(_go())
    mock_scope.assert_called_once_with(cfg, WORKSPACE, cfg.repo_root)
    mock_record.assert_called_once()
    assert mock_record.call_args.args[0] is sentinel_scoped_cfg
