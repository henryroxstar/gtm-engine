"""Minting an API key — the one place a raw ``sk-`` key is made (PRD 2026-10-19a, W1).

``POST /v1/api-keys`` and the one-call agent setup (``backend/services/provision.py``) both mint here, so the
key's shape, its stored hash and the entitlement cap cannot drift apart. The raw key exists only in the
return value: only its SHA-256 hash and an 8-character prefix are written, and nothing here logs it.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

_ENTITLEMENT_ORDER: dict[str, int] = {"free": 0, "pro": 1, "pro_plus": 2}


def cap_entitlement(requested: str, workspace_entitlement: str) -> str:
    """Return ``requested`` unless it exceeds ``workspace_entitlement``."""
    if _ENTITLEMENT_ORDER.get(requested, 0) > _ENTITLEMENT_ORDER.get(workspace_entitlement, 0):
        return workspace_entitlement
    return requested


async def insert_api_key(
    conn: Any,
    workspace_id: str,
    *,
    label: str,
    requested_entitlement: str,
    workspace_entitlement: str,
    agent_id: str | None,
) -> tuple[Any, str]:
    """Insert a key on an RLS-scoped connection; return ``(row, raw_key)``.

    The caller owns the transaction, so a key can be minted in the same transaction as the record that
    says it was (the setup job). The raw key is returned to the caller and to nobody else.
    """
    raw_key = "sk-" + secrets.token_urlsafe(32)
    row = await conn.fetchrow(
        """INSERT INTO api_keys(workspace_id, key_hash, prefix, label, entitlement, agent_id)
           VALUES($1::uuid, $2, $3, $4, $5, $6::uuid)
           RETURNING id::text, prefix, label, entitlement,
                     last_used_at, created_at, revoked_at, agent_id::text AS agent_id""",
        workspace_id,
        hashlib.sha256(raw_key.encode()).hexdigest(),
        raw_key[:8],
        label,
        cap_entitlement(requested_entitlement, workspace_entitlement),
        agent_id,
    )
    return row, raw_key
