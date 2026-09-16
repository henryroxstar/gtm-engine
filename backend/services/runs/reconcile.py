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
from .fake import FAKE_RUN_OFF_ERROR, _execute_fake_run, fake_runs_enabled, stamped_fake
from .pack_executor import _execute_pack_run
from .persistence import RunFailure, _fail_run
from .queue import _stamp_fake
from .state import _workspace_runs

log = logging.getLogger(__name__)

_RESTART_MESSAGE = "run did not survive a backend restart — start a new run"


_PACK_PROMPT_RE = re.compile(r"^\[pack\] (?P<pack>[^/\s]+)/(?P<variant>\S+) inputs=(?P<inputs>.*)$")


async def reconcile_gates(pool, repo_root) -> int:
    """Startup reconciliation (A5): resume pack runs stranded mid-gate by a restart.

    A process restart empties ``_gate_events``, so an ``awaiting_approval`` run has
    no task waiting on it — the run would hang forever even after the operator
    decides. For every such run we re-dispatch ``_execute_pack_run``: the runner
    reloads the durable manifest and RE-RUNS the still-gated node (awaiting_approval is
    not complete), so its draft may change; the gate loop then claims an already-recorded
    reject, or an approval made over the bytes now held, or re-opens the wait.

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
        job = await _resolve_job(pool, row)
        refusal = _refusal(job)
        if refusal is not None:
            await _fail_run(
                pool, row["workspace_id"], row["id"], refusal.error, error_code=refusal.code
            )
            continue
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
        await _take_ownership(pool, row["workspace_id"], row["id"])
        if fake_runs_enabled():
            # A dev restart (uvicorn reload) must resume a scripted run as a scripted run —
            # handing it to the real runner below would call the SDK. See services/runs/fake.py.
            if not job.get("fake"):
                # Stamped before it runs, as the queue does at dispatch: resumed unstamped,
                # the next restart with the flag off would hand it to the real runner.
                await _stamp_fake(pool, row["workspace_id"], row["id"])
            coro = _execute_fake_run(
                pool,
                repo_root,
                row["workspace_id"],
                row["id"],
                row["profile_name"],
                pack=job["pack"],
                variant=job["variant"],
            )
        else:
            coro = _execute_pack_run(
                pool,
                repo_root,
                row["workspace_id"],
                row["id"],
                row["profile_name"],
                job["pack"],
                job["variant"],
                job["inputs"],
                entitlement=entitlement,
                agent_id=row["agent_id"],
                agent_budget_usd=job.get("agent_budget_usd"),
                language=job.get("language"),
                dry_run=bool(job.get("dry_run")),
            )
        task = asyncio.create_task(coro)
        _workspace_runs.setdefault(row["workspace_id"], set()).add(row["id"])
        _track_run(task, pool, row["workspace_id"], row["id"])
        resumed += 1

    if rows:
        log.info(
            "gate reconciliation: resumed %d of %d awaiting_approval run(s)", resumed, len(rows)
        )
    return resumed


def _refusal(job: dict | None) -> RunFailure | None:
    """Why a gated run must be failed instead of resumed, or None to resume it.

    A run stamped fake that meets the flag off here (a dev restart after unsetting
    GTM_FAKE_RUNS) is refused: the real pack runner would spend, and could apply an approval
    recorded against the fake draft to a real one. See services/runs/fake.py."""
    if job is None:
        return RunFailure("run_interrupted", _RESTART_MESSAGE)
    if job.get("fake") and not fake_runs_enabled():
        return RunFailure("fake_runs_disabled", FAKE_RUN_OFF_ERROR)
    return None


async def _resolve_job(pool, row) -> dict | None:
    """The pack job to resume, or None for anything unresumable.

    Prefers ``runs.payload`` (V021), which carries dry_run/language/agent_budget_usd that
    the prompt-line regex silently dropped. Falls back to that regex for rows written
    BEFORE V021 — a rolling deploy leaves gated runs behind with a NULL payload, and
    failing them for that would turn a schema upgrade into lost work.
    """
    payload = None
    try:
        async with workspace_scope(pool, row["workspace_id"]) as conn:
            fetched = await conn.fetchrow(
                "SELECT payload FROM runs WHERE id = $1::uuid AND workspace_id = $2::uuid",
                row["id"],
                row["workspace_id"],
            )
        payload = fetched["payload"] if fetched else None
    except Exception:  # noqa: BLE001 — fall through to the pre-V021 path
        payload = None
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = None
    if isinstance(payload, dict) and payload.get("mode") == "pack" and payload.get("pack"):
        inputs = payload.get("inputs")
        return {
            "pack": payload["pack"],
            "variant": payload.get("variant"),
            "inputs": inputs if isinstance(inputs, dict) else {},
            "dry_run": payload.get("dry_run"),
            "language": payload.get("language"),
            "agent_budget_usd": payload.get("agent_budget_usd"),
            "fake": stamped_fake(payload),
        }
    if isinstance(payload, dict) and payload.get("mode") == "prompt":
        return None  # a prompt run's SDK session died with the process — unresumable

    match = _PACK_PROMPT_RE.match(row["prompt"] or "")
    if match is None:
        return None
    try:
        inputs = json.loads(match.group("inputs"))
    except (json.JSONDecodeError, TypeError):
        inputs = {}
    return {
        "pack": match.group("pack"),
        "variant": match.group("variant"),
        "inputs": inputs if isinstance(inputs, dict) else {},
        # A pre-V021 row resumed as a fake carries only the stamp in its payload.
        "fake": stamped_fake(payload),
    }


async def _take_ownership(pool, workspace_id: str, run_id: str) -> None:
    """Stamp this worker's claim + a fresh heartbeat on a run reconciliation just revived.

    Without it the run still has whatever lease the dead worker left (or none at all),
    so the queue's reclaimer would be free to claim and dispatch it a SECOND time
    moments later — reconciliation and the lease sweep racing over the same run."""
    from ...broker import WORKER_ID

    try:
        async with workspace_scope(pool, workspace_id) as conn:
            await conn.execute(
                """UPDATE runs SET claimed_by = $2, claimed_at = now(), heartbeat_at = now()
                   WHERE id = $1::uuid""",
                run_id,
                WORKER_ID,
            )
    except Exception:  # noqa: BLE001 — best-effort; the lease reclaim is idempotent enough
        log.warning("could not stamp ownership on resumed run %s", run_id, exc_info=True)
