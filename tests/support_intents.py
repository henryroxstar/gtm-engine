"""Test support: give a FAKE ledgers object the F4 dispatch-intent methods, in memory.

The dispatchers claim an intent through ``ledgers`` (``read_dispatch_rows`` / ``append_dispatch`` /
``claim_dispatch``). A fake that lacks them is refused fail-closed ("the intent ledger cannot be read"),
which is exactly right in production — so tests that fake the ledger attach this to behave like a working
one. Real-ledger tests (tests/agent/test_dispatch_wiring.py) do not use it.
"""

from __future__ import annotations

from typing import Any


def attach(ledgers: Any) -> Any:
    rows: list[dict] = []

    def read_dispatch_rows() -> list[dict]:
        return list(rows)

    def append_dispatch(record: dict) -> dict:
        rows.append(dict(record))
        return record

    def claim_dispatch(guard: Any, record: dict) -> tuple[Any, dict | None]:
        refused = guard(list(rows))
        if refused is not None:
            return refused, None
        rows.append(dict(record))
        return None, record

    ledgers.read_dispatch_rows = read_dispatch_rows
    ledgers.append_dispatch = append_dispatch
    ledgers.claim_dispatch = claim_dispatch
    return ledgers
