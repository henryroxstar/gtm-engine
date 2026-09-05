"""Startup gate reconciliation (A5): resume pack runs a restart stranded mid-gate; fail
prompt-mode runs explicitly (their SDK session died with the process)."""

from __future__ import annotations

import asyncio
import json
import logging
import re

from gtm_core.capabilities import Entitlement

from ...database import workspace_scope
from .budget import _track_run
from .pack_executor import _execute_pack_run
from .persistence import _fail_run

log = logging.getLogger(__name__)


_PACK_PROMPT_RE = re.compile(r"^\[pack\] (?P<pack>[^/\s]+)/(?P<variant>\S+) inputs=(?P<inputs>.*)$")


async def reconcile_gates(pool, repo_root) -> int:
    """Startup reconciliation (A5): resume pack runs stranded mid-gate by a restart.

    A process restart empties ``_gate_events``, so an ``awaiting_approval`` run has
    no task waiting on it — the run would hang forever even after the operator
    decides. For every such run we re-dispatch ``_execute_pack_run``: the runner
    reloads the durable manifest, returns at the still-gated node, and the gate
    loop either claims an already-recorded decision or re-opens the wait.

    Prompt-mode runs cannot be resumed (their SDK session died with the process) —
    they are failed explicitly rather than left hanging, so a client sees a
    terminal state instead of a run that never moves. Returns runs resumed.
    """
    # A4: this cross-tenant read runs on the runtime pool (gtm_api, no BYPASSRLS)
    # with no workspace context, so a plain `SELECT ... FROM runs` would return zero
    # rows under FORCE RLS. awaiting_approval_runs() is a SECURITY DEFINER function
    # (owned by gtm_bootstrap, V018) — the same audited pattern as
    # release_stale_reservations — that returns the awaiting_approval rows across all
    # tenants. Each row is still acted on below under workspace_scope.
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id::text AS id, workspace_id::text AS workspace_id, profile_name,
                          prompt, agent_id::text AS agent_id
                   FROM awaiting_approval_runs()"""
            )
    except Exception:  # noqa: BLE001 — reconciliation must never block boot
        log.exception("gate reconciliation: could not read awaiting_approval runs")
        return 0

    resumed = 0
    for row in rows:
        match = _PACK_PROMPT_RE.match(row["prompt"] or "")
        if match is None:
            await _fail_run(
                pool,
                row["workspace_id"],
                row["id"],
                "run did not survive a backend restart — start a new run",
            )
            continue
        try:
            inputs = json.loads(match.group("inputs"))
        except (json.JSONDecodeError, TypeError):
            inputs = {}
        # Re-fetch entitlement rather than trusting anything stored from before the
        # restart — the same "live, never cached" rule require_auth follows, so a
        # downgrade that happened while the run sat gated is honoured on resume. Uses
        # this module's own `workspace_scope` (not backend.deps.fetch_entitlement's)
        # so it goes through the exact same RLS/connection path — and the exact same
        # test-patch point — as every other query in this function.
        async with workspace_scope(pool, row["workspace_id"]) as conn:
            entitlement_row = await conn.fetchrow(
                "SELECT entitlement FROM subscriptions WHERE workspace_id = $1",
                row["workspace_id"],
            )
        entitlement = entitlement_row["entitlement"] if entitlement_row else Entitlement.FREE.value
        task = asyncio.create_task(
            _execute_pack_run(
                pool,
                repo_root,
                row["workspace_id"],
                row["id"],
                row["profile_name"],
                match.group("pack"),
                match.group("variant"),
                inputs if isinstance(inputs, dict) else {},
                entitlement=entitlement,
                agent_id=row["agent_id"],
            )
        )
        _track_run(task, pool, row["workspace_id"], row["id"])
        resumed += 1

    if rows:
        log.info(
            "gate reconciliation: resumed %d of %d awaiting_approval run(s)", resumed, len(rows)
        )
    return resumed
