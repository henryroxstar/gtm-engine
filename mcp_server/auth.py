"""API key validation for the MCP server.

The raw key (sk-...) is SHA-256 hex-hashed and resolved via resolve_api_key()
(V012) — a SECURITY DEFINER function owned by gtm_bootstrap. It does the hash
match + revoked/expiry filter + last_used_at bump in one round-trip and returns
only (key_id, workspace_id, entitlement). Returns an ApiKeyCtx or None.

Security:
- Raw key is never stored; only the hash hits the DB.
- resolve_api_key() is the pre-tenant bootstrap bypass (the lookup precedes any
  workspace context, so it cannot run RLS-scoped); every post-auth query runs
  RLS-subject as gtm_api inside workspace_scope(). See V012 / CLAUDE.md.
- The lookup is enumeration-safe: it resolves only a key whose SHA-256 hash the
  caller already presents, and exposes nothing beyond that key's own scope.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from gtm_core.capabilities import Entitlement


@dataclass(frozen=True)
class ApiKeyCtx:
    workspace_id: str
    entitlement: Entitlement
    key_id: str  # UUID of the api_keys row (for metering FK)


async def validate_api_key(raw_key: str, pool) -> ApiKeyCtx | None:
    """Validate a raw API key against the DB. Returns None if invalid."""
    if not raw_key or not raw_key.startswith("sk-"):
        return None
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM resolve_api_key($1)", key_hash)

    if not row:
        return None

    return ApiKeyCtx(
        workspace_id=str(row["workspace_id"]),
        entitlement=Entitlement(row["entitlement"]),
        key_id=str(row["key_id"]),
    )
