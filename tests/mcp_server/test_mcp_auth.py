"""Unit tests for mcp_server.auth — no DB required.

Tests validate_api_key's contract using a mock asyncpg pool. Post-V012 the lookup
is a single call to the SECURITY DEFINER resolver — `SELECT * FROM resolve_api_key($1)`
— which returns (key_id, workspace_id, entitlement) and folds the last_used_at bump in:
  - valid key returns ApiKeyCtx with correct workspace_id, entitlement, key_id
  - key with wrong prefix (not "sk-") is rejected without hitting the DB
  - empty key is rejected without hitting the DB
  - key not found / revoked / expired returns None (resolver returns no row)
  - entitlement from the DB row is mapped to Entitlement enum
  - the hash sent to the DB is sha256 hex of the raw key
"""

from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import AsyncMock, MagicMock

from gtm_core.capabilities import Entitlement
from mcp_server.auth import validate_api_key

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_pool(fetchrow_result):
    """Build a minimal mock asyncpg pool whose resolve_api_key returns fetchrow_result."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_result)

    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def _run(coro):
    return asyncio.run(coro)


_WS_ID = "aaaaaaaa-0000-0000-0000-aaaaaaaaaaaa"
_KEY_ID = "bbbbbbbb-0000-0000-0000-bbbbbbbbbbbb"
_RAW = "sk-test-valid-key-for-unit-tests"
_HASH = hashlib.sha256(_RAW.encode()).hexdigest()


def _row(entitlement: str = "pro") -> dict:
    """A resolve_api_key() result row (its RETURNS TABLE column names)."""
    return {"key_id": _KEY_ID, "workspace_id": _WS_ID, "entitlement": entitlement}


# ── tests ─────────────────────────────────────────────────────────────────────


def test_valid_key_returns_ctx():
    ctx = _run(validate_api_key(_RAW, _make_pool(_row())))
    assert ctx is not None
    assert ctx.workspace_id == _WS_ID
    assert ctx.entitlement == Entitlement.PRO
    assert ctx.key_id == _KEY_ID


def test_wrong_prefix_rejected_without_db():
    pool = _make_pool(None)
    result = _run(validate_api_key("notsk-badkey", pool))
    assert result is None
    pool.acquire.assert_not_called()


def test_empty_key_rejected():
    pool = _make_pool(None)
    result = _run(validate_api_key("", pool))
    assert result is None
    pool.acquire.assert_not_called()


def test_unknown_or_revoked_or_expired_returns_none():
    # resolver returns no row for unknown/revoked/expired keys
    assert _run(validate_api_key("sk-unknown-key", _make_pool(None))) is None


def test_pro_plus_entitlement_mapped():
    ctx = _run(validate_api_key(_RAW, _make_pool(_row("pro_plus"))))
    assert ctx.entitlement == Entitlement.PRO_PLUS


def test_free_entitlement_mapped():
    ctx = _run(validate_api_key(_RAW, _make_pool(_row("free"))))
    assert ctx.entitlement == Entitlement.FREE


def test_correct_hash_sent_to_resolver():
    """The hash passed to resolve_api_key must be sha256 hex of the raw key."""
    captured = {}

    async def _capture(query, key_hash):
        captured["query"] = query
        captured["hash"] = key_hash
        return _row()

    conn = MagicMock()
    conn.fetchrow = _capture
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    _run(validate_api_key(_RAW, pool))
    assert captured["hash"] == _HASH
    assert "resolve_api_key" in captured["query"]
