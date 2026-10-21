"""The hosted backend's dispatch-intent store (F4): ``dispatch_intents`` under FORCE RLS.

The twin of :class:`gtm_core.dispatch_intents.FileIntentStore` — same method names, same scenarios
(``tests/unit/dispatch_intent_scenarios.py``), and the SAME pure :func:`~gtm_core.dispatch_intents.decide`
doing the "may this batch proceed" thinking, so the two stores cannot disagree about what a repeat means.
Host-local files are the wrong place on a fleet that deploys and crashes routinely, and a tenant has no shell
for a resolve command — so the backend keeps this in Postgres, where the queue, the gate decision and the
event log already live.

Append-only (``gtm_api`` holds SELECT + INSERT only — see V046): a recipient's state is its LATEST row, and
release is a new row, never an UPDATE. Atomicity: a claim holds ``pg_advisory_xact_lock`` for the
``(workspace, sequence)`` pair, and a partial UNIQUE index on claim rows backstops it.
"""

from __future__ import annotations

from collections.abc import Sequence

import asyncpg

from gtm_core.dispatch_intents import (
    DISPATCHED,
    INTENT,
    OUTCOMES,
    RELEASED,
    RESOLUTIONS,
    UNKNOWN,
    ClaimResult,
    OpenBatch,
    decide,
    new_batch_id,
    refusal_message,
)

from ..database import workspace_scope

_STATE = {
    "claim": INTENT,
    "dispatched": DISPATCHED,
    "sent": DISPATCHED,
    "unknown": UNKNOWN,
    "failed": RELEASED,
    "not_sent": RELEASED,
    "removed": RELEASED,
}
_RELEASE_KINDS = ("failed", "not_sent", "removed")
_HOLD_SCOPE = "*"

_LATEST = """
    SELECT DISTINCT ON (recipient_key) recipient_key, kind, batch_id
    FROM dispatch_intents
    WHERE workspace_id = $1::uuid AND sequence_id = $2 AND recipient_key = ANY($3::text[])
    ORDER BY recipient_key, id DESC
"""

#: The recipients of one batch whose LATEST row is still that batch's own, in one of the given kinds.
_OWNED = """
    WITH mine AS (
        SELECT DISTINCT sequence_id, recipient_key FROM dispatch_intents
        WHERE workspace_id = $1::uuid AND batch_id = $2 AND kind = 'claim'
    ), latest AS (
        SELECT DISTINCT ON (d.sequence_id, d.recipient_key)
               d.sequence_id, d.recipient_key, d.kind, d.batch_id
        FROM dispatch_intents d
        JOIN mine m ON m.sequence_id = d.sequence_id AND m.recipient_key = d.recipient_key
        WHERE d.workspace_id = $1::uuid
        ORDER BY d.sequence_id, d.recipient_key, d.id DESC
    )
    SELECT sequence_id, recipient_key FROM latest WHERE batch_id = $2 AND kind = ANY($3::text[])
"""

_INSERT_EVENTS = """
    INSERT INTO dispatch_intents (workspace_id, batch_id, sequence_id, recipient_key, kind, detail)
    SELECT $1::uuid, $2, t.s, t.r, t.k, $3
    FROM unnest($4::text[], $5::text[], $6::text[]) AS t(s, r, k)
"""


class PgIntentStore:
    """``dispatch_intents`` for one workspace. Every method runs in its own RLS-scoped transaction."""

    def __init__(self, pool, workspace_id: str) -> None:
        self._pool = pool
        self._ws = workspace_id

    # -- reads ---------------------------------------------------------------------------------
    async def states(self, scope: str, recipients: Sequence[str]) -> dict[str, str]:
        async with workspace_scope(self._pool, self._ws) as conn:
            rows = await conn.fetch(_LATEST, self._ws, scope, list(recipients))
        return {r["recipient_key"]: _STATE[r["kind"]] for r in rows}

    async def hold_reason(self) -> str | None:
        async with workspace_scope(self._pool, self._ws) as conn:
            return await self._hold(conn)

    @staticmethod
    async def _hold(conn) -> str | None:
        kind = await conn.fetchval(
            "SELECT kind FROM dispatch_intents WHERE sequence_id = $1 AND kind IN ('hold', 'hold_cleared') "
            "ORDER BY id DESC LIMIT 1",
            _HOLD_SCOPE,
        )
        return "on hold" if kind == "hold" else None

    async def unresolved(self) -> list[OpenBatch]:
        async with workspace_scope(self._pool, self._ws) as conn:
            rows = await conn.fetch(
                """
                WITH latest AS (
                    SELECT DISTINCT ON (sequence_id, recipient_key)
                           sequence_id, recipient_key, kind, batch_id
                    FROM dispatch_intents
                    WHERE workspace_id = $1::uuid AND sequence_id <> $2
                    ORDER BY sequence_id, recipient_key, id DESC
                )
                SELECT l.batch_id, l.sequence_id, count(*) AS n,
                       (SELECT c.tool FROM dispatch_intents c
                         WHERE c.workspace_id = $1::uuid AND c.batch_id = l.batch_id AND c.kind = 'claim'
                         LIMIT 1) AS tool,
                       (SELECT c.label FROM dispatch_intents c
                         WHERE c.workspace_id = $1::uuid AND c.batch_id = l.batch_id AND c.kind = 'claim'
                         LIMIT 1) AS label,
                       (SELECT min(c.created_at) FROM dispatch_intents c
                         WHERE c.workspace_id = $1::uuid AND c.batch_id = l.batch_id
                           AND c.kind = 'claim') AS ts
                FROM latest l
                WHERE l.kind IN ('claim', 'unknown')
                GROUP BY l.batch_id, l.sequence_id
                ORDER BY ts
                """,
                self._ws,
                _HOLD_SCOPE,
            )
        return [
            OpenBatch(
                r["batch_id"],
                r["sequence_id"],
                r["tool"] or "",
                r["n"],
                UNKNOWN,  # an intent with no proven outcome and an explicit unknown read the same
                r["ts"].strftime("%Y-%m-%dT%H:%M:%SZ") if r["ts"] else "",
                r["label"],
            )
            for r in rows
        ]

    # -- writes --------------------------------------------------------------------------------
    async def claim(
        self,
        scope: str,
        recipients: Sequence[str],
        *,
        tool: str,
        label: str | None = None,
        ignore_hold: bool = False,
    ) -> ClaimResult:
        wanted = list(dict.fromkeys(recipients))
        if not wanted:
            msg = refusal_message("empty_batch", scope, (), count=0, label=label)
            return ClaimResult(False, None, "empty_batch", (), msg)
        batch_id = new_batch_id()
        try:
            return await self._claim_once(scope, wanted, tool, label, ignore_hold, batch_id)
        except asyncpg.UniqueViolationError:
            # Lost a race the advisory lock should already have prevented: the unique index is the
            # backstop. Re-evaluate once against the now-visible winner and refuse truthfully.
            return await self._claim_once(scope, wanted, tool, label, ignore_hold, batch_id)

    async def _claim_once(
        self,
        scope: str,
        wanted: list[str],
        tool: str,
        label: str | None,
        ignore_hold: bool,
        batch_id: str,
    ) -> ClaimResult:
        async with workspace_scope(self._pool, self._ws) as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"{self._ws}|{scope}")
            if not ignore_hold and await self._hold(conn) is not None:
                msg = refusal_message("on_hold", scope, (), count=len(wanted), label=label)
                return ClaimResult(False, None, "on_hold", (), msg)
            rows = await conn.fetch(_LATEST, self._ws, scope, wanted)
            states = {r["recipient_key"]: _STATE[r["kind"]] for r in rows}
            d = decide(states, wanted)
            if not d.ok:
                msg = refusal_message(
                    d.code or "", scope, d.overlap, count=len(wanted), label=label
                )
                owners = tuple(
                    dict.fromkeys(
                        r["batch_id"]
                        for r in rows
                        if r["recipient_key"] in d.overlap
                        and _STATE[r["kind"]] in (INTENT, UNKNOWN)
                    )
                )
                return ClaimResult(False, None, d.code, d.overlap, msg, owners)
            gens = {
                r["recipient_key"]: r["n"]
                for r in await conn.fetch(
                    "SELECT recipient_key, count(*) AS n FROM dispatch_intents "
                    "WHERE workspace_id = $1::uuid AND sequence_id = $2 AND recipient_key = ANY($3::text[]) "
                    "AND kind = ANY($4::text[]) GROUP BY recipient_key",
                    self._ws,
                    scope,
                    wanted,
                    list(_RELEASE_KINDS),
                )
            }
            await conn.execute(
                "INSERT INTO dispatch_intents "
                "(workspace_id, batch_id, sequence_id, recipient_key, kind, generation, tool, label) "
                "SELECT $1::uuid, $2, $3, t.r, 'claim', t.g, $4, $5 "
                "FROM unnest($6::text[], $7::int[]) AS t(r, g)",
                self._ws,
                batch_id,
                scope,
                tool,
                label,
                wanted,
                [gens.get(k, 0) + 1 for k in wanted],
            )
        return ClaimResult(True, batch_id, None, (), "")

    async def _append(
        self, conn, batch_id: str, targets: list[tuple[str, str, str]], detail=None
    ) -> None:
        if targets:
            await conn.execute(
                _INSERT_EVENTS,
                self._ws,
                batch_id,
                detail,
                [t[0] for t in targets],
                [t[1] for t in targets],
                [t[2] for t in targets],
            )

    async def record_outcome(self, batch_id: str, state: str, detail: str | None = None) -> None:
        if state not in OUTCOMES:
            raise ValueError(f"unknown outcome {state!r}")
        async with workspace_scope(self._pool, self._ws) as conn:
            owned = await conn.fetch(_OWNED, self._ws, batch_id, ["claim"])
            await self._append(
                conn,
                batch_id,
                [(r["sequence_id"], r["recipient_key"], state) for r in owned],
                detail,
            )

    async def resolve(
        self,
        batch_id: str,
        resolution: str,
        *,
        sent_recipients: Sequence[str] | None = None,
        actor: str | None = None,
    ) -> dict[str, int]:
        if resolution not in RESOLUTIONS:
            raise ValueError(f"unknown resolution {resolution!r}")
        sent = set(sent_recipients or ())
        async with workspace_scope(self._pool, self._ws) as conn:
            exists = await conn.fetchval(
                "SELECT 1 FROM dispatch_intents WHERE workspace_id = $1::uuid AND batch_id = $2 "
                "AND kind = 'claim' LIMIT 1",
                self._ws,
                batch_id,
            )
            if not exists:
                raise KeyError(batch_id)
            owned = await conn.fetch(_OWNED, self._ws, batch_id, ["claim", "unknown"])
            targets = []
            for r in owned:
                went = resolution == "sent" or (
                    resolution == "sent_recipients" and r["recipient_key"] in sent
                )
                targets.append(
                    (r["sequence_id"], r["recipient_key"], "sent" if went else "not_sent")
                )
            await self._append(conn, batch_id, targets, f"resolved by {actor}" if actor else None)
        n_sent = sum(1 for t in targets if t[2] == "sent")
        return {"sent": n_sent, "released": len(targets) - n_sent}

    async def mark_removed(
        self, scope: str, recipients: Sequence[str], actor: str | None = None
    ) -> None:
        async with workspace_scope(self._pool, self._ws) as conn:
            await self._append(
                conn,
                "removed",
                [(scope, r, "removed") for r in dict.fromkeys(recipients)],
                f"removed by {actor}" if actor else None,
            )

    async def set_hold(self, reason: str) -> None:
        async with workspace_scope(self._pool, self._ws) as conn:
            await self._append(conn, "hold", [(_HOLD_SCOPE, _HOLD_SCOPE, "hold")], reason)

    async def clear_hold(self) -> None:
        async with workspace_scope(self._pool, self._ws) as conn:
            await self._append(conn, "hold", [(_HOLD_SCOPE, _HOLD_SCOPE, "hold_cleared")])


def intent_store_for(pool, workspace_id: str):
    """The intent store for a backend dispatch: Postgres when there is a real pool.

    ``None`` (→ the dispatcher's default, the workspace's file ledger) only for a pool-less caller —
    unit tests that pass a bare ``object()``. A production pool always has ``acquire``.
    """
    return PgIntentStore(pool, workspace_id) if hasattr(pool, "acquire") else None
