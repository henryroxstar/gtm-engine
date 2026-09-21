"""Regression tests for the 2026-07-05 hardening follow-ups (flagged residuals).

Covers the behaviour-changing fixes:
  - content_sha is REQUIRED and enforced on every gate decision (H9 bypass closed)
  - the per-workspace run slot is freed idempotently AND even when a run task is
    cancelled before its body runs (the slot-leak fix)
  - the entitlement-sync handler records + applies a sync in ONE transaction, short-
    circuiting duplicate/stale syncs before the entitlement UPDATE (reorder TOCTOU).
    (Same guarantees the former RevenueCat webhook had; billing now owned by the billing service —
    see the billing-boundary design doc.)

SDK-free / mock-pool, asyncio.run per repo convention (see test_cost_guard.py).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from backend.routers import entitlement as entitlement_router
from backend.routers import runs as runs_router
from backend.schemas import GateRequest
from tests.backend._protocol1 import (  # noqa: E402
    SCOPE_MODULES,
    patch_everywhere,
)


def _scope(conn):
    @asynccontextmanager
    async def _s(pool, workspace_id):
        yield conn

    return _s


# ── #1 content_sha REQUIRED + enforced ────────────────────────────────────────


def test_gate_request_requires_content_sha():
    # Omitting content_sha is now a schema error (was silently optional → bypass).
    with pytest.raises(ValidationError):
        GateRequest(decision="approve")
    with pytest.raises(ValidationError):
        GateRequest(decision="approve", content_sha="")  # min_length=1


def _drive_decide_gate(pending_content: str, content_sha: str):
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "status": "awaiting_approval",
        "pending_gate": "⟦GATE:publish⟧",
        "pending_content": pending_content,
    }
    request = MagicMock()
    request.app.state.pool = MagicMock()
    ws = MagicMock()
    ws.workspace_id = "ws1"
    # Fleet Phase A (Task 3): decide_gate calls require_human(principal) first, which
    # admits only kind="user" — a bare MagicMock's auto-created .kind would refuse it.
    ws.kind = "user"
    body = GateRequest(decision="approve", content_sha=content_sha)

    async def _go():
        runs_router._gate_events["run1"] = asyncio.Event()
        runs_router._gate_decisions.pop("run1", None)
        try:
            with patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope(conn)):
                return await runs_router.decide_gate("run1", body, ws, request)
        finally:
            runs_router._gate_events.pop("run1", None)
            runs_router._gate_decisions.pop("run1", None)

    return asyncio.run(_go())


def test_decide_gate_accepts_matching_sha():
    good = runs_router._content_sha("the exact bytes")
    result = _drive_decide_gate("the exact bytes", good)
    assert result["decision"] == "approve"


def test_decide_gate_rejects_sha_mismatch():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        _drive_decide_gate("the exact bytes", "deadbeef" * 8)
    assert ei.value.status_code == 409


# ── #6 run slot: idempotent release + freed on pre-start cancel ────────────────


def test_release_run_slot_idempotent():
    runs_router._workspace_runs.clear()
    runs_router._workspace_runs["ws"] = {"r1", "r2"}
    runs_router._release_run_slot("ws", "r1")
    assert runs_router._workspace_runs["ws"] == {"r2"}
    runs_router._release_run_slot("ws", "r1")  # double release — no-op
    assert runs_router._workspace_runs["ws"] == {"r2"}
    runs_router._release_run_slot("ws", "r2")  # last one → key popped
    assert "ws" not in runs_router._workspace_runs
    runs_router._release_run_slot("ws", "never-reserved")  # unknown — safe


def test_track_run_frees_slot_on_completion():
    runs_router._workspace_runs.clear()
    runs_router._workspace_runs["ws"] = {"r1"}

    async def _go():
        async def _noop():
            return

        # pool arg is unused unless COST_RESERVATION_ENABLED (off here).
        t = runs_router._track_run(asyncio.create_task(_noop()), None, "ws", "r1")
        await t
        await asyncio.sleep(0)  # let the done-callback run

    asyncio.run(_go())
    assert "ws" not in runs_router._workspace_runs


def test_track_run_frees_slot_on_precancel():
    # The leak this fixes: a task cancelled by shutdown-drain BEFORE its body runs.
    runs_router._workspace_runs.clear()
    runs_router._workspace_runs["ws"] = {"r1"}

    async def _go():
        async def _sleep():
            await asyncio.sleep(10)

        t = runs_router._track_run(asyncio.create_task(_sleep()), None, "ws", "r1")
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0)

    asyncio.run(_go())
    assert "ws" not in runs_router._workspace_runs


# ── #5 entitlement sync: record + apply in one transaction, short-circuit dup/stale ──


def _drive_apply_sync(*, insert_result: str, newer: int, version):
    conn = AsyncMock()
    conn.fetchrow.return_value = {"lock": 1}  # FOR UPDATE lock row (truthy)
    conn.execute.side_effect = [insert_result, "UPDATE 1"]
    conn.fetchval.return_value = newer
    request = MagicMock()
    request.app.state.pool = MagicMock()

    async def _go():
        with patch.object(entitlement_router, "workspace_scope", _scope(conn)):
            return (
                await entitlement_router._apply_sync(
                    request, "ws1", "pro", 50.0, "active", "sync1", version
                ),
                conn,
            )

    return asyncio.run(_go())


def test_apply_sync_duplicate_skips_update():
    outcome, conn = _drive_apply_sync(insert_result="INSERT 0 0", newer=0, version=1000)
    assert outcome == "duplicate"
    assert conn.execute.await_count == 1  # INSERT only — no entitlement UPDATE


def test_apply_sync_stale_skips_update():
    outcome, conn = _drive_apply_sync(insert_result="INSERT 0 1", newer=1, version=1000)
    assert outcome == "stale"
    assert conn.execute.await_count == 1  # a newer sync already applied — no UPDATE


def test_apply_sync_new_applies_update():
    outcome, conn = _drive_apply_sync(insert_result="INSERT 0 1", newer=0, version=2000)
    assert outcome == "new"
    assert conn.execute.await_count == 2  # INSERT + entitlement UPDATE
    # The FOR UPDATE lock was taken before any write (serializes concurrent syncs).
    lock_sql = conn.fetchrow.call_args.args[0]
    assert "FOR UPDATE" in lock_sql
    update_sql = conn.execute.call_args_list[1].args[0]
    assert "plan_cost_cap_usd" in update_sql


# ── A9: CORS production boot guard (Track A, 2026-08-09) ─────────────────────
# allow_credentials=True + a wildcard origin is a cross-origin credential leak.
# The guard is a pure function so these cases need no app and no env mutation.


def test_cors_wildcard_or_unset_raises_in_production():
    from backend.main import check_cors_origins

    for raw in (None, "", "*", "https://app.example.com,*"):
        with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
            check_cors_origins(raw, "production")


def test_cors_explicit_origins_allowed_in_production():
    from backend.main import check_cors_origins

    assert check_cors_origins("https://app.example.com", "production") == [
        "https://app.example.com"
    ]
    assert check_cors_origins(
        "https://app.example.com, https://admin.example.com", "production"
    ) == ["https://app.example.com", "https://admin.example.com"]


def test_cors_permissive_default_kept_outside_production():
    """Local dev keeps the wildcard — the guard is a *production* deploy gate."""
    from backend.main import check_cors_origins

    assert check_cors_origins(None, "development") == ["*"]
    assert check_cors_origins("*", "test") == ["*"]


# ── Dev-secret boot guard ─────────────────────────────────────────────────────
# deploy/.env.dev.example commits deliberately PUBLIC values for BACKEND_JWT_SECRET and
# BILLING_SYNC_SECRET. Copied into a real config they make every token forgeable and every
# entitlement grantable, so outside ENV=development the backend refuses to boot on them.

_DEV_ENV_FILE = Path(__file__).resolve().parents[2] / "deploy" / ".env.dev.example"
_NON_DEV_ENVS = [None, "", "production", "staging", "test", "Development"]
# Low-entropy stand-ins for a Doppler-injected value — none starts with the dev prefix.
_REAL_LOOKING = ("x" * 48, "not-local-dev-prefixed-" + "y" * 24)


def _dev_values() -> tuple[str, str]:
    """The two secrets exactly as the committed dev env file sets them. deploy/ is private and
    omitted from the public carve, so there the file-backed cases skip rather than fail."""
    if not _DEV_ENV_FILE.is_file():
        pytest.skip("deploy/.env.dev.example is not in this tree (omitted from the public carve)")
    values = {}
    for line in _DEV_ENV_FILE.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.lstrip().startswith("#"):
            values[key.strip()] = value.strip()
    return values["BACKEND_JWT_SECRET"], values["BILLING_SYNC_SECRET"]


def test_dev_secret_prefix_covers_both_committed_dev_values():
    """The guard's prefix is only a guard if the dev file's values actually carry it."""
    from backend.main import _DEV_SECRET_PREFIX

    for value in _dev_values():
        assert value.startswith(_DEV_SECRET_PREFIX), "dev env value escaped the guard prefix"


def test_dev_secrets_boot_in_development():
    from backend.main import check_no_dev_secrets

    check_no_dev_secrets("development", *_dev_values())


@pytest.mark.parametrize("env", _NON_DEV_ENVS)
def test_either_dev_secret_refuses_to_boot_outside_development(env):
    from backend.main import check_no_dev_secrets

    jwt_dev, billing_dev = _dev_values()
    real = _REAL_LOOKING[0]
    cases = [
        ((jwt_dev, real), "BACKEND_JWT_SECRET"),
        ((real, billing_dev), "BILLING_SYNC_SECRET"),
        ((jwt_dev, billing_dev), "BACKEND_JWT_SECRET"),
    ]
    for pair, named in cases:
        with pytest.raises(RuntimeError, match=named) as refused:
            check_no_dev_secrets(env, *pair)
        # The refusal names the variable, never the value.
        assert jwt_dev not in str(refused.value) and billing_dev not in str(refused.value)


@pytest.mark.parametrize("env", [*_NON_DEV_ENVS, "development"])
def test_real_looking_secrets_boot_everywhere(env):
    from backend.main import check_no_dev_secrets

    check_no_dev_secrets(env, *_REAL_LOOKING)
    check_no_dev_secrets(env, _REAL_LOOKING[1], _REAL_LOOKING[0])


@pytest.mark.parametrize("env", _NON_DEV_ENVS)
def test_empty_or_unset_secrets_are_not_this_guards_concern(env):
    from backend.main import check_no_dev_secrets

    for value in (None, ""):
        check_no_dev_secrets(env, value, value, value)


# VAULT_KEK cannot carry the "local-dev-" prefix the other two do: get_kek() requires valid
# 32-byte hex, and no ASCII marker survives that constraint. So its committed dev placeholder
# is guarded by exact match instead — a real Doppler KEK that merely happens to be valid hex
# must never be mistaken for it (test_a_real_looking_vault_kek_boots_everywhere below).


def _dev_vault_kek() -> str:
    """VAULT_KEK exactly as the committed dev env file sets it."""
    if not _DEV_ENV_FILE.is_file():
        pytest.skip("deploy/.env.dev.example is not in this tree (omitted from the public carve)")
    for line in _DEV_ENV_FILE.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "VAULT_KEK":
            return value.strip()
    raise AssertionError("VAULT_KEK not found in deploy/.env.dev.example")


def test_dev_vault_kek_constant_matches_the_committed_dev_value():
    """The guard's exact-match constant is only a guard if it matches the dev file's value."""
    from backend.main import _DEV_VAULT_KEK

    assert _DEV_VAULT_KEK == _dev_vault_kek()


def test_dev_vault_kek_boots_in_development():
    from backend.main import check_no_dev_secrets

    check_no_dev_secrets("development", *_dev_values(), _dev_vault_kek())


@pytest.mark.parametrize("env", _NON_DEV_ENVS)
def test_dev_vault_kek_refuses_to_boot_outside_development(env):
    from backend.main import check_no_dev_secrets

    kek_dev = _dev_vault_kek()
    real = _REAL_LOOKING[0]
    with pytest.raises(RuntimeError, match="VAULT_KEK") as refused:
        check_no_dev_secrets(env, real, real, kek_dev)
    # The refusal names the variable, never the value.
    assert kek_dev not in str(refused.value)


@pytest.mark.parametrize("env", [*_NON_DEV_ENVS, "development"])
def test_a_real_looking_vault_kek_boots_everywhere(env):
    from backend.main import check_no_dev_secrets

    # Valid 32-byte hex, like a real Doppler-generated KEK — exact match only, never "is hex".
    real_kek = "ab" * 32
    check_no_dev_secrets(env, *_REAL_LOOKING, real_kek)


def test_lifespan_refuses_a_dev_vault_kek_before_touching_the_database(monkeypatch):
    """Wiring proof for VAULT_KEK, same shape as the JWT/billing case above — and the
    highest-blast-radius of the three: a leaked KEK is non-revocable, since rotating it
    does not retroactively re-encrypt every already-stored encrypted_credentials row."""
    from fastapi import FastAPI

    from backend import main as backend_main

    monkeypatch.delenv("GTM_FAKE_RUNS", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("BACKEND_JWT_SECRET", raising=False)
    monkeypatch.delenv("BILLING_SYNC_SECRET", raising=False)
    monkeypatch.setenv("VAULT_KEK", _dev_vault_kek())

    async def _boot():
        async with backend_main.lifespan(FastAPI()):
            pass

    with pytest.raises(RuntimeError, match="VAULT_KEK"):
        asyncio.run(_boot())


def test_lifespan_refuses_a_dev_secret_before_touching_the_database(monkeypatch):
    """Wiring, not just the function: with DATABASE_URL unset a missing guard would surface
    as a KeyError, so a RuntimeError naming the secret proves the lifespan calls it."""
    from fastapi import FastAPI

    from backend import main as backend_main

    monkeypatch.delenv("GTM_FAKE_RUNS", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("BILLING_SYNC_SECRET", _dev_values()[1])

    async def _boot():
        async with backend_main.lifespan(FastAPI()):
            pass

    with pytest.raises(RuntimeError, match="BILLING_SYNC_SECRET"):
        asyncio.run(_boot())


# ── #241 Q6: edited content at a gate with no draft is refused, not silently dropped ──


def _drive_decide_gate_kind(gate_kind, decision, edited_content=None):
    pending = "stub"
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "status": "awaiting_approval",
        "pending_gate": "⟦GATE:plan⟧",
        "pending_content": pending,
        "gate_kind": gate_kind,
    }
    request = MagicMock()
    request.app.state.pool = MagicMock()
    ws = MagicMock()
    ws.workspace_id = "ws1"
    # Fleet Phase A (Task 3): decide_gate calls require_human(principal) first, which
    # admits only kind="user" — a bare MagicMock's auto-created .kind would refuse it.
    ws.kind = "user"
    body = GateRequest(
        decision=decision,
        content_sha=runs_router._content_sha(pending),
        edited_content=edited_content,
    )

    async def _go():
        runs_router._gate_events["run1"] = asyncio.Event()
        runs_router._gate_decisions.pop("run1", None)
        try:
            with patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope(conn)):
                return await runs_router.decide_gate("run1", body, ws, request)
        finally:
            runs_router._gate_events.pop("run1", None)
            runs_router._gate_decisions.pop("run1", None)

    return asyncio.run(_go())


@pytest.mark.parametrize(
    ("decision", "edited"), [("edit", "trimmed drafts"), ("approve", "trimmed drafts")]
)
def test_decide_gate_refuses_edited_content_at_a_review_gate(decision, edited):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        _drive_decide_gate_kind("review", decision, edited)
    assert ei.value.status_code == 422


@pytest.mark.parametrize("gate_kind", ["plan", "email_enroll", None])
def test_decide_gate_still_accepts_edits_where_a_draft_or_prompt_run_applies_them(gate_kind):
    result = _drive_decide_gate_kind(gate_kind, "edit", "edited bytes")
    assert result["decision"] == "edit"


def test_decide_gate_still_accepts_plain_approve_and_reject_at_a_review_gate():
    assert _drive_decide_gate_kind("review", "approve")["decision"] == "approve"
    assert _drive_decide_gate_kind("review", "reject")["decision"] == "reject"
