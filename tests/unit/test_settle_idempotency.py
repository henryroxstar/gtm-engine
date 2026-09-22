"""Tests for settle_credits idempotency and meter_call atomicity.

BUG-4: calling settle_credits twice on the same reservation_id must only
deduct credits once (the second call finds state != 'open' and skips).

R1: if the ameter audit insert fails after the wallet UPDATE in meter_call,
the error must be caught and logged rather than silently swallowed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gtm_core.metering import settle_credits

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


# ── R1: meter_call ameter failure is caught ───────────────────────────────────


def test_meter_call_logs_warning_when_ameter_fails():
    """If ameter raises after the wallet UPDATE, meter_call must catch it and
    log a warning so the discrepancy is visible for reconciliation."""
    from mcp_server.meter import meter_call

    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock(return_value="UPDATE 1")
    mock_conn.fetchval = AsyncMock(return_value=_WS_ID)

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("mcp_server.meter.ameter", AsyncMock(side_effect=RuntimeError("db insert boom"))):
        with patch("mcp_server.meter.workspace_scope") as mock_ws_scope:
            # Make workspace_scope an async context manager that yields mock_conn
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=mock_conn)
            ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ws_scope.return_value = ctx

            # Should NOT raise — the exception must be caught
            with pytest.warns(match=r"") if False else _assert_logs_warning():
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
                        pool=mock_pool,
                    )
                )


class _assert_logs_warning:
    """Context manager that asserts a WARNING log is emitted."""

    def __enter__(self):
        self._handler = logging.Handler()
        self._handler.emit = MagicMock()
        self._handler.setLevel(logging.WARNING)
        self._logger = logging.getLogger("mcp_server.meter")
        self._logger.addHandler(self._handler)
        return self

    def __exit__(self, *exc):
        self._logger.removeHandler(self._handler)
        calls = self._handler.emit.call_args_list
        warnings = [c for c in calls if c[0][0].levelno >= logging.WARNING]
        assert len(warnings) >= 1, (
            "Expected at least one WARNING log from meter_call when ameter fails"
        )
        return False
