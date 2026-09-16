"""Prompt-mode run lifecycle (``_execute_run``): stream the agent session, detect gate
sentinels in the output, hold the gate, persist every transition.

The transitions themselves live in :mod:`.lifecycle`; what stays here is the half the
behavioral-diff table classifies as deliberately prompt-specific — the SDK stream, the
sentinel scan over the accumulated text, and the entitlement-only skill scope.
"""

from __future__ import annotations

import contextlib

from gtm_core.capabilities import Entitlement

from .budget import _reserve_or_deny
from .gate_kinds import _GATE_PLAN_SENTINEL, _GATE_PUBLISH_SENTINEL, prompt_gate_kind
from .lifecycle import (
    CANCELLED,
    TIMED_OUT,
    approved,
    complete_run,
    hold_gate,
    reject_run,
    resume_run,
    rewrite_pending_content,
    start_run,
)
from .persistence import _CAP_REACHED, _fail_run
from .state import _cancelled_runs

_SENTINELS = (_GATE_PLAN_SENTINEL, _GATE_PUBLISH_SENTINEL)


async def _execute_run(
    pool,
    sessions,
    workspace_id: str,
    run_id: str,
    profile_name: str,
    prompt: str,
    dry_run: bool,
    *,
    entitlement: Entitlement | str,
    agent_id: str | None = None,
) -> None:
    """Background task: run the pipeline, persist stage outputs, handle gates.

    ``entitlement`` is REQUIRED (no default) on purpose. Prompt mode ran unscoped
    until 2026-08-25 because the scope was only ever wired into ``_execute_pack_run``
    — a caller that simply forgot the argument is exactly how that hole opened, so
    there is nothing here to forget. It is the live value ``require_auth`` read for
    this request, never a cached one.
    """
    try:
        # Guard: run was cancelled before background task started
        if run_id in _cancelled_runs:
            _cancelled_runs.discard(run_id)
            return

        # Pre-call budget gate (§R2): block before any paid brain call if the workspace
        # is at/over its monthly cost cap. Fail-closed on the backend paid route. Runs
        # RLS-subject. With COST_RESERVATION_ENABLED this atomically reserves a slot
        # (exact under concurrency); otherwise it is today's acheck_budget read.
        if not await _reserve_or_deny(pool, workspace_id, run_id):
            await _fail_run(pool, workspace_id, run_id, _CAP_REACHED, error_code="cost_cap_reached")
            return
        await start_run(pool, workspace_id, run_id)

        output_buf: list[str] = []
        handled_gates: set[str] = set()

        # Commercial skill scope for the free-text path. Prompt mode has no graph, so
        # there is no run-level min_entitlement to check (that is pack mode's 403) —
        # the per-skill floors in gtm_core/gating.toml ARE the boundary here, and this
        # is what makes them enforceable: the session carries the set as the SDK
        # `skills=` allowlist and as the can_use_tool deny scope. Registry ∩
        # entitlement, NOT pack reachability — see gating.entitled_skills.
        from gtm_core.gating import entitled_skills

        allowed_skills = entitled_skills(entitlement)

        # agent_id passed only when set: pre-A4 session fakes (and the store's old
        # signature) keep working for agent-less runs.
        _run_kwargs = {"agent_id": agent_id} if agent_id is not None else {}
        stream = sessions.run(
            pool,
            workspace_id,
            profile_name,
            prompt,
            run_id,
            allowed_skills=allowed_skills,
            **_run_kwargs,
        )
        # RL-02: `aclosing` guarantees the session store's own try/finally (return the
        # session to its warm cache, or close it) still runs on the early `return` below
        # — abandoning a partially-consumed async generator without this leaves that
        # cleanup to rely on GC timing, which is not deterministic and can leak the SDK
        # subprocess/session for a while.
        async with contextlib.aclosing(stream):
            async for chunk in stream:
                # Checked on EVERY chunk (a cheap set-membership check) — not only at the
                # loop's start/end, so a cancel arriving mid-stream stops pulling further
                # chunks off the SDK session rather than draining it to exhaustion. This
                # chunk was already produced by the generator before we could know the
                # run was cancelled, so it is discarded here rather than processed.
                if run_id in _cancelled_runs:
                    _cancelled_runs.discard(run_id)
                    return
                output_buf.append(chunk)
                combined = "".join(output_buf)
                for sentinel in _SENTINELS:
                    # Each sentinel fires AT MOST once. output_buf is never trimmed, so
                    # a consumed sentinel stays in `combined` for the rest of the stream;
                    # tracking handled sentinels (rather than a single reset-to-None flag)
                    # stops the next chunk from re-detecting and re-blocking the SAME gate
                    # — otherwise an approved gate re-opens on every subsequent chunk and
                    # the run can never progress past its first gate.
                    if sentinel not in combined or sentinel in handled_gates:
                        continue
                    handled_gates.add(sentinel)
                    # A prompt run holds its gate in-process only: no run_gates row,
                    # because the SDK session dies with the process and cannot be resumed
                    # (diff-table row 7 — reconcile_gates fails these explicitly rather
                    # than pretending).
                    decision = await hold_gate(
                        pool,
                        workspace_id,
                        run_id,
                        sentinel=sentinel,
                        gate=prompt_gate_kind(sentinel),
                        pending_content=combined,
                    )
                    if decision in (CANCELLED, TIMED_OUT):
                        return
                    if not approved(decision):
                        await reject_run(pool, workspace_id, run_id)
                        return
                    # Approved — re-gate before spending more, so a long multi-gate run
                    # can't overshoot the cap set at start (H6). Same reserve/deny path as
                    # the start guard (reserves an additional slot when the flag is on).
                    if not await _reserve_or_deny(pool, workspace_id, run_id):
                        await _fail_run(
                            pool,
                            workspace_id,
                            run_id,
                            _CAP_REACHED,
                            error_code="cost_cap_reached",
                        )
                        return
                    # Approve-with-edits: when the operator supplies edited bytes, those
                    # become this run's content of record. Replace the streamed-so-far
                    # buffer so the persisted `output` is EXACTLY what was approved, and
                    # rewrite `pending_content` so the gate's audit row (and any
                    # downstream consumer) sees the approved bytes, not the original
                    # draft. NOTE: the backend records the approved content; the
                    # server-side publish CALL is not wired in this runtime (it lives in
                    # the Telegram cockpit via agent/publish.py), so this cannot ship the
                    # wrong bytes.
                    edited = decision.get("edited_content")
                    if edited is not None:
                        output_buf.clear()
                        output_buf.append(edited)
                        await rewrite_pending_content(pool, workspace_id, run_id, edited)
                    # Flip the run back to 'running' and clear pending_gate/
                    # pending_content (RL-09) — a prompt run has no run_gates row, so
                    # `resume_run`'s write to `runs` is the ONLY record that this gate
                    # closed. Mirrors pack_executor._execute_pack_run's post-approval
                    # resume_run call.
                    await resume_run(pool, workspace_id, run_id)
                    # Continue streaming. `handled_gates` already contains this
                    # sentinel, so it will not re-fire; do NOT clear it.

        # Guard: run was cancelled right as the stream finished naturally.
        if run_id in _cancelled_runs:
            _cancelled_runs.discard(run_id)
            return

        await complete_run(pool, workspace_id, run_id, "".join(output_buf))
    except Exception as exc:  # noqa: BLE001
        try:
            await _fail_run(pool, workspace_id, run_id, str(exc), error_code="internal_error")
        except Exception:  # noqa: BLE001
            pass  # nosec B110 — intentional best-effort swallow
