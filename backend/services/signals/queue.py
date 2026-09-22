"""Postgres-backed headless signal queue (PRD 2026-09-20).

Decouples high-frequency ingestion skills (market-harvest, events-tracker) from
LLM reasoning pipelines, enforcing:
1. Exact-once processing via Postgres `FOR UPDATE SKIP LOCKED`.
2. PII Claim-Check Pattern (§R9): `payload_ref` is an opaque VFS URI/path pointer;
   raw prospect PII is never stored in table rows.
3. Crash-loop bounds: rows exceeding `MAX_ATTEMPTS` (3) transition to `failed`.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3

# Basic heuristic to reject inline PII or large JSON blobs in payload_ref
_PII_OR_BLOB_PATTERN = re.compile(
    r"""
    [\{\}\[\]]             # raw JSON braces
    | [a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+  # raw email address
    | (https?://)?(www\.)?linkedin\.com/in/           # personal LinkedIn profile
    | \+?[0-9]{10,15}                                 # phone numbers
    """,
    re.VERBOSE,
)


def validate_payload_ref(payload_ref: str) -> None:
    """Enforce the PII Claim-Check Pattern (§R9 / PRD §3.2).

    The pointer must name a path or URI, never embed raw prospect data.
    """
    if not payload_ref or not isinstance(payload_ref, str):
        raise ValueError(f"Invalid payload_ref: {payload_ref!r}")

    if len(payload_ref) > 1024:
        raise ValueError(f"payload_ref exceeds length limit: {len(payload_ref)}")

    if _PII_OR_BLOB_PATTERN.search(payload_ref):
        raise ValueError(
            f"payload_ref violates §R9 PII claim-check rule (must be an opaque path/URI): {payload_ref!r}"
        )


async def enqueue_signal(
    pool: Any,
    profile_name: str,
    signal_type: str,
    payload_ref: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Insert a new signal into the queue.

    If a signal with the same `idempotency_key` already exists, returns the existing record.
    """
    validate_payload_ref(payload_ref)

    if not profile_name or not signal_type or not idempotency_key:
        raise ValueError("profile_name, signal_type, and idempotency_key must be non-empty")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO headless_signals (
                profile_name, signal_type, payload_ref, idempotency_key, status, attempts
            )
            VALUES ($1, $2, $3, $4, 'pending', 0)
            ON CONFLICT (idempotency_key) DO UPDATE
                SET updated_at = now()
            RETURNING id, profile_name, signal_type, payload_ref, idempotency_key, status, attempts, created_at
            """,
            profile_name,
            signal_type,
            payload_ref,
            idempotency_key,
        )
        return dict(row)


async def claim_next_signal(
    pool: Any,
    worker_id: str,
    lease_s: int = 90,
) -> dict[str, Any] | None:
    """Claim one pending or expired signal for processing using FOR UPDATE SKIP LOCKED.

    If a claimed signal has exceeded MAX_ATTEMPTS, it is marked as failed and skipped.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM claim_next_signal($1, $2)",
            worker_id,
            lease_s,
        )
        if not row:
            return None

        res = dict(row)
        if res["attempts"] > MAX_ATTEMPTS:
            await fail_signal(
                pool,
                res["id"],
                error=f"Signal exceeded max attempts ({MAX_ATTEMPTS})",
            )
            return None

        return res


async def complete_signal(pool: Any, signal_id: str | UUID) -> bool:
    """Mark a signal as successfully processed."""
    sig_uuid = UUID(str(signal_id))
    async with pool.acquire() as conn:
        status = await conn.execute(
            """
            UPDATE headless_signals
               SET status = 'completed',
                   updated_at = now()
             WHERE id = $1 AND status = 'processing'
            """,
            sig_uuid,
        )
        return status == "UPDATE 1"


async def fail_signal(pool: Any, signal_id: str | UUID, error: str) -> bool:
    """Mark a signal as permanently failed."""
    sig_uuid = UUID(str(signal_id))
    async with pool.acquire() as conn:
        status = await conn.execute(
            """
            UPDATE headless_signals
               SET status = 'failed',
                   error = $2,
                   updated_at = now()
             WHERE id = $1 AND status != 'completed'
            """,
            sig_uuid,
            error,
        )
        return status == "UPDATE 1"


async def get_signal(pool: Any, signal_id: str | UUID) -> dict[str, Any] | None:
    """Fetch signal by ID."""
    sig_uuid = UUID(str(signal_id))
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM headless_signals WHERE id = $1",
            sig_uuid,
        )
        return dict(row) if row else None
