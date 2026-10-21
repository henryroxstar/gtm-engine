"""Tests for settle_credits idempotency, its failure contract, and meter_call atomicity.

BUG-4: calling settle_credits twice on the same reservation_id must only
deduct credits once (the second call finds state != 'open' and skips).

MM3: settle_credits' default is unchanged (a failure returns False), but with
``raise_on_error=True`` a failure raises DebitFailedError — and meter_call, which is now a
thin adapter on it, surfaces a failed audit insert instead of leaving a debited wallet with no
ledger row ("reconcile manually", the old R1 behaviour).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from gtm_core.metering import DebitFailedError, settle_credits

_WS_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_RUN_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
_RESERVATION_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


def _run(coro):
    return asyncio.run(coro)


# ── helpers ───────────────────────────────────────────────────────────────────


class _FakeConn:
    """Minimal async connection stub that tracks state transitions."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self._reservation_state = "open"

    async def fetchrow(self, sql: str, *args: Any):
        self.executed.append((sql, args))
        if "cost_reservations" in sql:
            # Only return a row if the query matches the current state
            if "state = 'open'" in sql and self._reservation_state != "open":
                return None
            if self._reservation_state == "open":
                return {
                    "workspace_id": _WS_ID,
                    "run_id": _RUN_ID,
                    "estimated_credits": 10.0,
                    "state": self._reservation_state,
                }
            return None
        return None

    async def fetchval(self, sql: str, *args: Any):
        self.executed.append((sql, args))
        if "set_config" in sql:
            return _WS_ID
        return None

    async def execute(self, sql: str, *args: Any):
        self.executed.append((sql, args))
        # Track state transition when the reservation is settled
        if "UPDATE cost_reservations" in sql and "state = 'settled'" in sql:
            self._reservation_state = "settled"
        return "UPDATE 1"


# ── BUG-4: settle_credits idempotency ─────────────────────────────────────────


def test_settle_credits_idempotent_only_deducts_once():
    """Calling settle_credits twice for the same reservation_id must deduct
    from workspace_wallets exactly once — the second call should find no
    'open' reservation and skip the deduction."""
    conn = _FakeConn()

    # First settle — should succeed and deduct
    result1 = _run(
        settle_credits(
            _RESERVATION_ID,
            5.0,
            conn=conn,
        )
    )
    assert result1 is True

    # Count wallet UPDATEs after first call
    wallet_updates_after_first = [
        (sql, args) for sql, args in conn.executed if "UPDATE workspace_wallets" in sql
    ]
    assert len(wallet_updates_after_first) == 1, (
        "First settle should produce exactly one wallet UPDATE"
    )

    # Second settle — same reservation, should be a no-op for the wallet
    result2 = _run(
        settle_credits(
            _RESERVATION_ID,
            5.0,
            conn=conn,
        )
    )
    assert result2 is True

    # Count wallet UPDATEs after both calls
    wallet_updates_total = [
        (sql, args) for sql, args in conn.executed if "UPDATE workspace_wallets" in sql
    ]
    assert len(wallet_updates_total) == 1, (
        f"Double-settle deducted credits {len(wallet_updates_total)} times; "
        f"expected exactly 1 wallet UPDATE across both calls"
    )


def test_settle_credits_settled_reservation_returns_true_but_skips_deduct():
    """A reservation that is already 'settled' should return True (idempotent
    success) but not touch the wallet."""
    conn = _FakeConn()
    conn._reservation_state = "settled"  # pre-settled

    result = _run(
        settle_credits(
            _RESERVATION_ID,
            5.0,
            conn=conn,
        )
    )
    assert result is True

    wallet_updates = [
        (sql, args) for sql, args in conn.executed if "UPDATE workspace_wallets" in sql
    ]
    assert len(wallet_updates) == 0, (
        "Settling an already-settled reservation should NOT update the wallet"
    )


# ── MM3: the failure contract ─────────────────────────────────────────────────


class _TxCtx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


class _FailingConn(_FakeConn):
    """A conn whose statements raise when ``fail_on`` appears in the SQL."""

    def __init__(self, fail_on: str) -> None:
        super().__init__()
        self._fail_on = fail_on

    async def execute(self, sql: str, *args: Any):
        if self._fail_on in sql:
            raise RuntimeError("db boom")
        return await super().execute(sql, *args)


def test_settle_credits_default_still_returns_false_on_a_failed_debit():
    """The default is unchanged, so every existing backend caller behaves exactly as before."""
    conn = _FailingConn("UPDATE workspace_wallets")
    assert _run(settle_credits(_RESERVATION_ID, 5.0, conn=conn)) is False


def test_settle_credits_raise_on_error_raises_debit_failed_error():
    conn = _FailingConn("UPDATE workspace_wallets")
    with pytest.raises(DebitFailedError) as failed:
        _run(settle_credits(_RESERVATION_ID, 5.0, conn=conn, raise_on_error=True))
    # The database's own error rides along: it is what the operator's log line will show.
    assert isinstance(failed.value.__cause__, RuntimeError)
    assert "db boom" in str(failed.value.__cause__)


def test_settle_credits_raise_on_error_raises_when_there_is_nothing_to_debit():
    """No connection and no pool+workspace: the default returns False, which a caller can ignore.
    With raise_on_error a debit that was never attempted is a failed debit too."""
    assert _run(settle_credits(_RESERVATION_ID, 5.0)) is False
    with pytest.raises(DebitFailedError):
        _run(settle_credits(_RESERVATION_ID, 5.0, raise_on_error=True))


def test_settle_credits_raise_on_error_covers_a_failure_outside_the_statements():
    """Acquiring the scoped connection can fail before any settle statement runs; with
    raise_on_error that is a failed debit too, not an unrelated exception."""
    pool = MagicMock()
    pool.acquire.side_effect = RuntimeError("pool exhausted")
    with pytest.raises(DebitFailedError):
        _run(settle_credits("", 5.0, pool=pool, workspace_id=_WS_ID, raise_on_error=True))


def test_settle_credits_raise_on_error_does_not_raise_for_a_good_settle():
    """The other half: raise_on_error must not simply raise on everything."""
    assert _run(settle_credits(_RESERVATION_ID, 5.0, conn=_FakeConn(), raise_on_error=True))


# ── meter_call is a thin adapter on settle_credits ────────────────────────────


def test_meter_call_surfaces_an_audit_insert_failure():
    """The debit and its audit row are ONE settle now. If the audit insert fails, that is a failed
    debit — raised as DebitFailedError and marked on the wallet — not a logged "credits deducted
    but audit row missing". (That the failed settle rolls the wallet debit back with it is proven
    on a real database in tests/mcp_server/test_wallet_debt_live.py::...[audit].)"""
    from mcp_server.meter import meter_call

    conn = _FailingConn("INSERT INTO unified_metering_log")
    conn.transaction = lambda: _TxCtx()
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    with pytest.raises(DebitFailedError):
        _run(
            meter_call(
                workspace_id=_WS_ID,
                api_key_id="key-1",
                tool_name="draft_post",
                profile_name="test",
                model="gpt-4",
                prompt_tokens=100,
                completion_tokens=50,
                cost_usd=0.01,
                pool=pool,
            )
        )

    marker_writes = [sql for sql, _ in conn.executed if "debit_failed_at" in sql]
    assert len(marker_writes) == 1, "the failure must leave the durable marker"
