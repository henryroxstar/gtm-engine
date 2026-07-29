"""Unified cost-metering contract across all four runtimes (VPS, Backend, MCP).

One canonical record schema (:class:`CostRecord`), one cache-aware brain-cost
formula (:func:`brain_cost_usd`), one best-effort writer (:func:`meter` / its
async twin :func:`ameter`), one pre-call budget guard (:func:`check_budget` /
:func:`acheck_budget`), and two sinks:

  - :class:`JsonlSink` → ``content/<profile>/costs.jsonl`` (VPS volume). Wraps the
    existing :class:`gtm_core.ledgers.Ledgers` so the established JSONL reader
    (``Ledgers.month_cost_total``) keeps working
    unchanged. The brain + 4 workers that already call ``append_cost`` are
    *conformant by output* — :meth:`CostRecord.to_jsonl_dict` reproduces the exact
    shape they emit, so they are left untouched (a contract test pins this).
  - :class:`PgSink` → ``cost_records`` (Backend) or ``mcp_calls`` (MCP). The new
    Backend writer the runtime was missing, and the single home for the SQL the
    MCP meter already had (``mcp_server/meter.py`` becomes a thin adapter).

Design doc: docs/prds/2026-06-19-cost-usage-tracking.md (§5). This module is
stdlib-only at import time — no SDK, no asyncpg, no ``agent`` import — so the
import-light MCP workers can use it without pulling heavy deps. The Postgres pool
is duck-typed (an object with an async ``acquire()`` context manager), exactly as
``mcp_server/meter.py`` already treats it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from gtm_core.models import ModelSpec

# Anthropic prompt-cache billing multipliers (a property of the cache *model*, not
# of any one model id — they apply identically to Sonnet, Haiku, etc., so they live
# here as the single canonical home rather than per-role in the registry):
#   - a 5-minute cache WRITE bills ~1.25x the input rate
#   - a cache READ bills ~0.10x the input rate
CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10

# Fallback rates if neither an env override nor a registry rate is available. List
# pricing for the configured brain model (claude-sonnet-4-6: $3/1M in, $15/1M out).
_DEFAULT_INPUT_USD_PER_1K = 0.003
_DEFAULT_OUTPUT_USD_PER_1K = 0.015

# The two Postgres cost tables this contract knows how to write. Their column sets
# and timestamp columns differ; the map is the single place that difference lives.
# NOTE: table names are an internal allowlist (never user input) — safe to format
# into SQL after membership is checked against this dict.
_PG_TABLES: dict[str, dict[str, Any]] = {
    "cost_records": {
        "ts_col": "recorded_at",
        "columns": (
            "workspace_id",
            "profile_name",
            "run_id",
            "stage",
            "model",
            "input_tokens",
            "output_tokens",
            "cost_usd",
        ),
    },
    "mcp_calls": {
        "ts_col": "called_at",
        "columns": (
            "workspace_id",
            "api_key_id",
            "tool_name",
            "profile_name",
            "model",
            "prompt_tokens",
            "completion_tokens",
            "cost_usd",
        ),
    },
}


def _utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string with a trailing ``Z`` (matches Ledgers)."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class CostRecord:
    """One metered call — the canonical shape every runtime/source produces.

    Carries the union of fields any sink needs; each sink projects out the subset
    its store uses. Exactly one scope axis is authoritative per runtime (``profile``
    on the VPS volume, ``workspace_id`` in the cloud), but both are cheap to carry.
    ``user_id`` is populated now (one workspace == one user in V1) so a V2 teams
    migration never has to backfill attribution (PRD §7.1).
    """

    runtime: str  # "vps" | "backend" | "mcp"
    source: str  # "brain" | "deepseek" | "vision" | "higgsfield" | "elevenlabs" | ...
    cost_usd: float
    model_or_sku: str | None = None
    op: str | None = None
    # scope
    profile: str | None = None
    workspace_id: str | None = None
    user_id: str | None = None
    api_key_id: str | None = None  # MCP only (mcp_calls.api_key_id)
    run_id: str | None = None
    stage: str | None = None
    # usage — whichever applies to the source
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    units: float | None = (
        None  # e.g. units=1 unit_kind="video_5s" / units=18000 unit_kind="tts_char"
    )
    unit_kind: str | None = None
    ts: str | None = None  # filled by the sink at write time if None
    extra: dict[str, Any] = field(
        default_factory=dict
    )  # source-specific notes (e.g. credit-estimate)

    def to_jsonl_dict(self) -> dict[str, Any]:
        """Project to the exact JSONL shape the existing costs.jsonl readers consume.

        Back-compat is load-bearing: ``Ledgers.month_cost_total`` keys on ``ts`` +
        ``cost_usd`` and legacy readers group by the ``tool`` field.
        So ``source`` maps to ``"tool"`` and we keep the worker/brain field names
        (``model``, ``input_tokens``, ``cache_*``). ``None``/empty values are dropped
        so a record never carries noise the legacy writers wouldn't have emitted.
        """
        out: dict[str, Any] = {
            "tool": self.source,
            "cost_usd": round(float(self.cost_usd), 6),
        }
        if self.ts:
            out["ts"] = self.ts
        if self.profile:
            out["profile"] = self.profile
        if self.op:
            out["op"] = self.op
        if self.model_or_sku:
            out["model"] = self.model_or_sku
        if self.run_id:
            out["run_id"] = self.run_id
        if self.stage:
            out["stage"] = self.stage
        if self.input_tokens:
            out["input_tokens"] = self.input_tokens
        if self.output_tokens:
            out["output_tokens"] = self.output_tokens
        if self.cache_creation_input_tokens:
            out["cache_creation_input_tokens"] = self.cache_creation_input_tokens
        if self.cache_read_input_tokens:
            out["cache_read_input_tokens"] = self.cache_read_input_tokens
        if self.units is not None:
            out["units"] = self.units
        if self.unit_kind:
            out["unit_kind"] = self.unit_kind
        for k, v in self.extra.items():
            out.setdefault(k, v)
        return out


# --------------------------------------------------------------------------- #
# Brain cost — the one cache-aware formula (shared by VPS session + Backend).   #
# --------------------------------------------------------------------------- #


def brain_cost_usd(
    usage: dict | None,
    *,
    input_usd_per_1k: float,
    output_usd_per_1k: float,
) -> float:
    """Cache-aware token→USD for an Anthropic brain call.

    ``cost = (input + cache_write*1.25 + cache_read*0.10)/1k * R_in + output/1k * R_out``.

    Rates are passed in (resolved per-role by the caller — env override → registry
    spec → default) so the cost *follows the resolved model*: a ``brain_plan →
    brain_cheap`` (Sonnet → Haiku) fallback bills Haiku rates, not Sonnet's. Pure;
    safe to unit-test on raw numbers.
    """
    u = usage or {}
    input_tokens = int(u.get("input_tokens", 0) or 0)
    output_tokens = int(u.get("output_tokens", 0) or 0)
    cache_creation = int(u.get("cache_creation_input_tokens", 0) or 0)
    cache_read = int(u.get("cache_read_input_tokens", 0) or 0)
    return round(
        (input_tokens + cache_creation * CACHE_WRITE_MULT + cache_read * CACHE_READ_MULT)
        / 1000.0
        * input_usd_per_1k
        + output_tokens / 1000.0 * output_usd_per_1k,
        6,
    )


def resolve_rates(
    spec: ModelSpec | None,
    *,
    env_input: str,
    env_output: str,
) -> tuple[float, float]:
    """Resolve (input, output) per-1k rates: env override → registry spec → default.

    Mirrors the worker/vision pattern: a break-glass env var wins, else the rate the
    registry pins for the role's model (so changing the model in ``models.toml``
    changes the billed rate in the same edit), else list-pricing defaults.
    """
    import os

    spec_in = getattr(spec, "input_usd_per_1k", None)
    spec_out = getattr(spec, "output_usd_per_1k", None)
    r_in = float(
        os.getenv(env_input) or (spec_in if spec_in is not None else _DEFAULT_INPUT_USD_PER_1K)
    )
    r_out = float(
        os.getenv(env_output) or (spec_out if spec_out is not None else _DEFAULT_OUTPUT_USD_PER_1K)
    )
    return r_in, r_out


# --------------------------------------------------------------------------- #
# Sinks                                                                         #
# --------------------------------------------------------------------------- #


@runtime_checkable
class Sink(Protocol):
    """Synchronous sink (VPS / JSONL)."""

    def write(self, rec: CostRecord) -> None: ...
    def month_total(self, scope: str | None = None) -> float: ...


class JsonlSink:
    """Sync sink over ``content/<profile>/costs.jsonl`` — wraps :class:`Ledgers`.

    ``write`` is literally ``Ledgers.append_cost(rec.to_jsonl_dict())`` and
    ``month_total`` is ``Ledgers.month_cost_total`` — so a record written through
    this sink is summed by the same reader the budget guard uses, and the legacy
    writers remain bit-compatible.
    """

    def __init__(self, cfg: Any, profile: str) -> None:
        from gtm_core.ledgers import Ledgers

        self._ledgers = Ledgers(cfg, profile)
        self._profile = profile

    def write(self, rec: CostRecord) -> None:
        payload = rec.to_jsonl_dict()
        payload.setdefault("profile", self._profile)
        self._ledgers.append_cost(payload)

    def month_total(self, scope: str | None = None) -> float:
        # scope is ignored: a JsonlSink is already bound to one profile's ledger.
        return self._ledgers.month_cost_total()


class PgSink:
    """Async sink over a Postgres cost table (``cost_records`` or ``mcp_calls``).

    The pool is duck-typed (an object exposing ``async with pool.acquire() as conn``)
    — no asyncpg import here, matching ``mcp_server/meter.py``.
    """

    def __init__(self, pool: Any, table: str) -> None:
        if table not in _PG_TABLES:
            raise ValueError(f"unknown cost table {table!r} (expected one of {sorted(_PG_TABLES)})")
        self._pool = pool
        self._table = table
        self._spec = _PG_TABLES[table]

    def _row_for_table(self, rec: CostRecord) -> tuple[Any, ...]:
        """Project a CostRecord onto the target table's ordered column tuple."""
        cost = round(float(rec.cost_usd), 6)
        if self._table == "cost_records":
            return (
                rec.workspace_id,
                rec.profile or "",
                rec.run_id,
                rec.stage,
                rec.model_or_sku,
                rec.input_tokens,
                rec.output_tokens,
                cost,
            )
        # mcp_calls
        return (
            rec.workspace_id,
            rec.api_key_id,
            rec.op or rec.source,  # tool_name
            rec.profile or "",
            rec.model_or_sku,
            rec.input_tokens,  # prompt_tokens
            rec.output_tokens,  # completion_tokens
            cost,
        )

    async def awrite(self, rec: CostRecord, *, conn: Any = None) -> None:
        """Insert one cost record.

        Pass ``conn`` to write on an already-RLS-scoped connection (the Backend runs
        RLS-subject, so the INSERT's ``WITH CHECK (workspace_id = current_workspace_id())``
        only passes inside a ``workspace_scope``). Omit ``conn`` to acquire a raw pool
        connection (the MCP role bypasses RLS as table owner — its prior behavior).
        """
        cols = self._spec["columns"]
        placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
        sql = f"INSERT INTO {self._table}({', '.join(cols)}) VALUES ({placeholders})"  # noqa: S608
        args = self._row_for_table(rec)
        if conn is not None:
            await conn.execute(sql, *args)
            return
        async with self._pool.acquire() as c:
            await c.execute(sql, *args)

    async def cap_and_spent(
        self, workspace_id: str, *, conn: Any = None
    ) -> tuple[float, float] | None:
        """Return (cap_usd, month_to_date_spent_usd) for the workspace, or None.

        One query joining ``subscriptions`` to this table's current-month rows — the
        same shape the original MCP guard used, generalised over the table. Pass
        ``conn`` to read on an RLS-scoped connection (Backend); omit it to read on a
        raw pool connection (MCP / owner-bypass).
        """
        ts_col = self._spec["ts_col"]
        sql = f"""
            SELECT s.monthly_cost_cap_usd AS cap,
                   COALESCE(SUM(t.cost_usd), 0) AS spent
            FROM   subscriptions s
            LEFT JOIN {self._table} t
                   ON t.workspace_id = s.workspace_id
                  AND date_trunc('month', t.{ts_col} AT TIME ZONE 'UTC')
                    = date_trunc('month', now() AT TIME ZONE 'UTC')
            WHERE  s.workspace_id = $1
            GROUP  BY s.monthly_cost_cap_usd
        """  # noqa: S608 — ts_col/table come from the internal _PG_TABLES allowlist
        if conn is not None:
            row = await conn.fetchrow(sql, workspace_id)
        else:
            async with self._pool.acquire() as c:
                row = await c.fetchrow(sql, workspace_id)
        if row is None:
            return None
        return float(row["cap"]), float(row["spent"])


# --------------------------------------------------------------------------- #
# meter() — best-effort write                                                   #
# --------------------------------------------------------------------------- #


def meter(rec: CostRecord, *, sink: Sink) -> None:
    """Append a cost record via a sync sink. Best-effort — never raises."""
    try:
        if rec.ts is None:
            rec = _with_ts(rec)
        sink.write(rec)
    except Exception:  # noqa: BLE001 — metering must never break the metered call
        return


async def ameter(rec: CostRecord, *, sink: PgSink, conn: Any = None) -> None:
    """Insert a cost record via an async (Postgres) sink. Best-effort — never raises.

    ``conn`` is forwarded to the sink so a Backend write can run on an RLS-scoped
    connection; omit it for the MCP / owner-bypass path.
    """
    try:
        if rec.ts is None:
            rec = _with_ts(rec)
        await sink.awrite(rec, conn=conn)
    except Exception:  # noqa: BLE001 — metering must never break the metered call
        return


def _with_ts(rec: CostRecord) -> CostRecord:
    from dataclasses import replace

    return replace(rec, ts=_utc_now_iso())


# --------------------------------------------------------------------------- #
# Budget guard (§R2)                                                            #
# --------------------------------------------------------------------------- #


def check_budget(
    scope: str | None,
    cap_usd: float,
    *,
    sink: Sink,
    hard_ceiling_usd: float | None = None,
) -> bool:
    """Sync pre-call guard (VPS). Return True if a paid call is allowed.

    Policy:
      - Read month-to-date total from the sink; deny when ``total >= cap_usd``.
      - ``hard_ceiling_usd`` (typically 2x cap) is defense-in-depth on the readable
        path: deny when ``total >= hard_ceiling_usd`` even if ``cap_usd`` is
        misconfigured (e.g. accidentally huge).
      - On a *read error* fail **open** (allow): the SDK per-run ``max_budget_usd``
        is the hard backstop for the unreadable case, so a transient ledger hiccup
        must not block a legitimate run.
    """
    try:
        total = sink.month_total(scope)
    except Exception:  # noqa: BLE001
        return True  # fail-open; per-run SDK cap backstops the unreadable case
    if hard_ceiling_usd is not None and total >= hard_ceiling_usd:
        return False
    return total < cap_usd


async def acheck_budget(
    pool: Any,
    workspace_id: str,
    *,
    table: str,
    hard_ceiling_multiplier: float | None = 2.0,
    conn: Any = None,
    fail_closed: bool = False,
) -> bool:
    """Async pre-call guard (Backend / MCP). Return True if a paid call is allowed.

    Reads ``(cap, spent)`` from ``subscriptions`` joined to ``table`` in one query.
    Policy: deny at/over cap, deny over the hard ceiling (``cap * multiplier``).

    ``fail_closed`` selects the error/missing-row policy:
      - ``False`` (default, VPS/MCP): a DB read error or missing subscription row
        allows the call — the SDK per-run ``max_budget_usd`` backstops the
        unreadable case, so a transient hiccup never blocks a legitimate run.
      - ``True`` (backend paid route): the SAME conditions **deny**. A network-
        facing tenant path must not open the paid gate when the budget can't be
        confirmed. The backend still keeps the per-run SDK cap as a second bound.

    Pass ``conn`` to read on an RLS-scoped connection (Backend); omit it for the MCP /
    owner-bypass path (``pool`` is then used to acquire a raw connection).
    """
    try:
        result = await PgSink(pool, table).cap_and_spent(workspace_id, conn=conn)
    except Exception:  # noqa: BLE001
        return not fail_closed  # fail-open (VPS/MCP) vs fail-closed (backend paid)
    if result is None:
        return not fail_closed  # no subscription row → allow unless fail-closed
    cap, spent = result
    if hard_ceiling_multiplier and spent >= cap * hard_ceiling_multiplier:
        return False
    return spent < cap


async def areserve_budget(
    conn: Any,
    workspace_id: str,
    *,
    run_id: str | None,
    estimate: float,
    table: str = "cost_records",
    fail_closed: bool = True,
) -> str | None:
    """Atomically reserve budget for a paid run — the exact-under-concurrency gate.

    Returns the new reservation id (str) when admitted, else ``None`` (denied). Unlike
    :func:`acheck_budget` (a read that a concurrent run can race), this SERIALIZES per
    workspace: it takes ``SELECT monthly_cost_cap_usd ... FOR UPDATE`` on the workspace's
    ``subscriptions`` row, then admits ``estimate`` only if
    ``month_to_date_spent + Σ(open reservations) + estimate <= cap`` and inserts an
    ``open`` ``cost_reservations`` row — all in one transaction. Two concurrent reserves
    for the same workspace serialize on the row lock, each sees the other's open
    reservation, and the sum can never exceed ``cap``. Closes the check-then-act TOCTOU
    (PRD 2026-07-06 §3.2, Phase 1).

    MUST be called with a ``conn`` already inside a :func:`gtm_core.db.workspace_scope`
    transaction (V009 RLS): the FOR UPDATE lock is held across the INSERT, and the INSERT
    satisfies ``cost_reservations``' ``WITH CHECK (workspace_id = current_workspace_id())``.

    ``fail_closed=True`` (the backend paid route): a DB error or a missing subscription
    row DENIES — consistent with today's ``acheck_budget(fail_closed=True)``; the SDK
    per-run ``max_budget_usd`` stays a second bound.

    ``estimate`` is a fixed conservative per-run value in Phase 1
    (``RESERVATION_ESTIMATE_USD``). **Phase 2 hook:** replace it with a per-stage estimate
    derived from ``cost_records`` percentiles — blocked on pricing decision #2. Do not
    change this function's shape for Phase 2; pass a better ``estimate`` in.
    """
    if table not in _PG_TABLES:
        raise ValueError(f"unknown cost table {table!r} (expected one of {sorted(_PG_TABLES)})")
    ts_col = _PG_TABLES[table]["ts_col"]
    try:
        # FOR UPDATE serializes concurrent reserves for THIS workspace (different
        # workspaces never contend — it is a per-row lock).
        cap = await conn.fetchval(
            "SELECT monthly_cost_cap_usd FROM subscriptions WHERE workspace_id = $1 FOR UPDATE",
            workspace_id,
        )
        if cap is not None:
            # Month-to-date recorded spend — same UTC-month shape as PgSink.cap_and_spent.
            spent = await conn.fetchval(
                f"SELECT COALESCE(SUM(cost_usd), 0) FROM {table} "  # noqa: S608 — table from allowlist
                f"WHERE workspace_id = $1 "
                f"  AND date_trunc('month', {ts_col} AT TIME ZONE 'UTC') "
                f"    = date_trunc('month', now() AT TIME ZONE 'UTC')",
                workspace_id,
            )
            open_sum = await conn.fetchval(
                "SELECT COALESCE(SUM(estimated_usd), 0) FROM cost_reservations "
                "WHERE workspace_id = $1 AND state = 'open'",
                workspace_id,
            )
            if float(spent) + float(open_sum) + float(estimate) > float(cap):
                return None  # denied — admitting this reservation would breach the cap
        elif fail_closed:
            return None  # no subscription row → deny on the paid route
        # Admitted: under cap, or no-sub with fail_open. Insert the open reservation.
        return await conn.fetchval(
            "INSERT INTO cost_reservations(workspace_id, run_id, estimated_usd) "
            "VALUES($1, $2::uuid, $3) RETURNING id::text",
            workspace_id,
            run_id,
            estimate,
        )
    except Exception:  # noqa: BLE001
        # Paid route is fail-closed: a DB error on the reserve path denies (the SDK
        # per-run cap backstops). Never raise — a metering hiccup must not 500 a run.
        return None
