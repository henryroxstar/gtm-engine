"""Persistence for ``provisioning_jobs`` (V047): get-or-create, updates, and the lease (W1; see ``provision.py``)."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import HTTPException, status

from ..database import workspace_scope
from .provision_model import ACTIVE, LEASE_S, PARK_EXPIRY_DAYS, RETRY_AFTER_S, ProvisionRequest

log = logging.getLogger(__name__)

_COLS = (
    "j.id::text AS id, j.workspace_id::text AS workspace_id, j.idempotency_key, j.request_hash, "
    "j.source_key, j.agent_name, j.state, j.step, j.onboard_job_id::text AS onboard_job_id, "
    "j.profile_slug, j.agent_id::text AS agent_id, j.key_id::text AS key_id, j.integrations, "
    "j.blockers, j.errors, j.lease_expires_at, j.expires_at, j.created_at, j.updated_at, "
    "(SELECT k.prefix FROM api_keys k WHERE k.id = j.key_id) AS key_prefix"
)
_SETTABLE = {
    "state",
    "step",
    "onboard_job_id",
    "profile_slug",
    "agent_id",
    "key_id",
    "integrations",
    "blockers",
    "errors",
}
_JSONB = {"blockers", "errors"}
_UUID = {"onboard_job_id", "agent_id", "key_id"}


async def _update(conn: Any, workspace_id: str, job_id: str, **cols: Any) -> dict:
    """UPDATE a job (only allow-listed columns) and return the fresh row."""
    sets, args = [], []
    for col, val in cols.items():
        if col not in _SETTABLE:
            raise ValueError(f"not a settable provisioning column: {col}")
        if col in _JSONB:
            args.append(json.dumps(val))
            sets.append(f"{col} = ${len(args)}::jsonb")
        elif col in _UUID:
            args.append(val)
            sets.append(f"{col} = ${len(args)}::uuid")
        else:
            args.append(val)
            sets.append(f"{col} = ${len(args)}")
    args.extend([job_id, workspace_id])
    row = await conn.fetchrow(
        f"UPDATE provisioning_jobs j SET {', '.join(sets)}, updated_at = now() "  # noqa: S608 # nosec B608 — columns are allow-listed above
        f"WHERE j.id = ${len(args) - 1}::uuid AND j.workspace_id = ${len(args)}::uuid "
        f"RETURNING {_COLS}",
        *args,
    )
    return dict(row)


async def _fetch_by_key(conn: Any, workspace_id: str, key: str) -> dict | None:
    row = await conn.fetchrow(
        f"SELECT {_COLS} FROM provisioning_jobs j "  # noqa: S608 # nosec B608 — module constant
        "WHERE j.workspace_id = $1::uuid AND j.idempotency_key = $2",
        workspace_id,
        key,
    )
    return dict(row) if row else None


async def _fetch_active_for_source(conn: Any, workspace_id: str, source_key: str) -> dict | None:
    row = await conn.fetchrow(
        f"SELECT {_COLS} FROM provisioning_jobs j "  # noqa: S608 # nosec B608 — module constant
        "WHERE j.workspace_id = $1::uuid AND j.source_key = $2 AND j.state = ANY($3::text[])",
        workspace_id,
        source_key,
        sorted(ACTIVE),
    )
    return dict(row) if row else None


async def _get_or_create(
    pool: Any, ws_id: str, key: str, req_hash: str, source_key: str, req: ProvisionRequest
) -> tuple[dict, bool]:
    """``(job, is_this_keys_own_job)``. ``False`` means another key's active job for the same source."""
    async with workspace_scope(pool, ws_id) as conn:
        # An active job past its expiry is over; free its slot before anything looks at it.
        await conn.execute(
            "UPDATE provisioning_jobs SET state = 'abandoned', updated_at = now() "
            "WHERE workspace_id = $1::uuid AND state = ANY($2::text[]) AND expires_at <= now() "
            "AND (idempotency_key = $3 OR source_key = $4)",
            ws_id,
            sorted(ACTIVE),
            key,
            source_key,
        )
        existing = await _fetch_by_key(conn, ws_id, key)
        if existing is None:
            await conn.execute(
                "INSERT INTO provisioning_jobs(workspace_id, idempotency_key, request_hash, source_key, "
                "agent_name, state, expires_at) VALUES($1::uuid, $2, $3, $4, $5, 'started', "
                "now() + make_interval(days => $6)) ON CONFLICT DO NOTHING",
                ws_id,
                key,
                req_hash,
                source_key,
                req.name,
                PARK_EXPIRY_DAYS,
            )
            # No pre-check for another key's active job: the partial unique index IS the dedupe, so a
            # check-then-insert could only ever agree with it or lose a race to it. Re-read: either our own
            # row, or (when the insert conflicted) the key's existing row or the source's active one.
            existing = await _fetch_by_key(conn, ws_id, key)
            if existing is None:
                active = await _fetch_active_for_source(conn, ws_id, source_key)
                if active is None:  # pragma: no cover - a conflict row vanished mid-transaction
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        {"code": "provision_in_progress", "message": "Setup is already running."},
                    )
                return active, False
    if existing["request_hash"] != req_hash:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "idempotency_key_reused",
                "message": "This Idempotency-Key was used for a different setup. Use a new key.",
            },
        )
    return existing, True


async def _acquire_lease(pool: Any, ws_id: str, job: dict) -> dict:
    async with workspace_scope(pool, ws_id) as conn:
        row = await conn.fetchrow(
            f"UPDATE provisioning_jobs j SET lease_expires_at = now() + make_interval(secs => $3), "  # noqa: S608 # nosec B608
            f"updated_at = now() WHERE j.id = $1::uuid AND j.workspace_id = $2::uuid "
            f"AND (j.lease_expires_at IS NULL OR j.lease_expires_at <= now()) RETURNING {_COLS}",
            job["id"],
            ws_id,
            LEASE_S,
        )
        if row is not None:
            return dict(row)
        wait = await conn.fetchval(
            "SELECT GREATEST(1, CEIL(EXTRACT(EPOCH FROM (lease_expires_at - now()))))::int "
            "FROM provisioning_jobs WHERE id = $1::uuid AND workspace_id = $2::uuid",
            job["id"],
            ws_id,
        )
    retry = int(wait or RETRY_AFTER_S)
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        {
            "code": "provision_in_progress",
            "message": f"This setup is still running. Retry after {retry} seconds.",
            "retry_after_s": retry,
        },
        headers={"Retry-After": str(retry)},
    )


async def _release_lease(pool: Any, ws_id: str, job_id: str) -> None:
    try:
        async with workspace_scope(pool, ws_id) as conn:
            await conn.execute(
                "UPDATE provisioning_jobs SET lease_expires_at = NULL "
                "WHERE id = $1::uuid AND workspace_id = $2::uuid",
                job_id,
                ws_id,
            )
    except Exception:  # noqa: BLE001 — the lease simply lapses
        log.warning("could not release a provisioning lease", exc_info=True)
