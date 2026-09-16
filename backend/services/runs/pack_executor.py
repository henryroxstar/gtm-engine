"""Pack-mode run lifecycle (``_execute_pack_run``): drive the graph runner with gates held
OUTSIDE it (return-and-resume), durable gate rows, per-node persistence + events, artifact
attribution, and the Gate-2 dispatch — A10 for publish, A11 for email enrollment."""

from __future__ import annotations

import json

from gtm_core.capabilities import Entitlement
from gtm_core.metering import acheck_budget

from ...database import workspace_scope
from ...email_dispatch import dispatch_backend_email_enroll
from ...publish_dispatch import dispatch_backend_publish
from .budget import _reserve_or_deny
from .decisions import run_status
from .events import _utc_now, publish_run_event
from .gate_kinds import _GATE_PLAN_SENTINEL, dispatch_target, pack_gate_kind
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
from .persistence import (
    _CAP_REACHED,
    RunFailure,
    _fail_run,
    _file_block,
    _persist_node,
    _upsert_block,
    enroll_refusal,
    publish_refusal,
)
from .state import _cancelled_runs

# The per-workspace concurrency slot is freed by the task done-callback set in
# create_run (_track_run) — guaranteed on success, error, or cancellation.


def _budget_guard(
    pool, workspace_id: str, run_id: str, agent_id: str | None, agent_budget_usd: float | None
):
    """The §R2 per-batch predicate the runner calls before EVERY dispatch batch, the list
    of its verdicts so far (a run the guard stopped is not reported as a node failure), and
    a PARALLEL list recording which of those ``False`` verdicts were RL-02's cancel check
    rather than a real budget exhaustion — ``agent/pipeline.py``'s runner treats every
    ``False`` identically (fail the batch's first node with "cost cap reached"), so the
    caller needs this to avoid mislabeling a cancelled run as a cost-cap failure.

    RL-02: a run cancelled while the runner is mid-dispatch (not parked at a gate) has no
    other seam to stop it — the graph runner (``agent/pipeline.py``, never modified here)
    only exposes ``budget_ok()`` as a per-batch checkpoint. Deliberately narrow: this reads
    THIS run's own ``runs.status`` for exactly ``"canceled"``, never a generic "any
    terminal status stops dispatch" rule — every other terminal status already means the
    runner returned before ``_budget_guard`` could be called again.
    """
    verdicts: list[bool] = []
    cancelled: list[bool] = []

    async def _budget_ok() -> bool:
        # Guard against the backend's ledger of record (Postgres cost_records) — the
        # runner's default JSONL read would see $0 here. A4: the per-agent budget can only
        # NARROW the workspace verdict (effective cap = min(workspace cap, agent budget)),
        # re-derived at every spend check so an entitlement downgrade bites immediately.
        from ...agents import acheck_agent_budget

        async with workspace_scope(pool, workspace_id) as conn:
            row = await conn.fetchrow(
                "SELECT status FROM runs WHERE id = $1::uuid AND workspace_id = $2::uuid",
                run_id,
                workspace_id,
            )
            if row is not None and row["status"] == "canceled":
                verdicts.append(False)
                cancelled.append(True)
                return False
            ok = await acheck_budget(
                pool, workspace_id, table="cost_records", conn=conn, fail_closed=True
            ) and await acheck_agent_budget(conn, workspace_id, agent_id, agent_budget_usd)
        verdicts.append(ok)
        cancelled.append(False)
        return ok

    return _budget_ok, verdicts, cancelled


def _node_observer(pool, workspace_id: str, run_id: str, profile_name: str, profile_root):
    """A2 protocol-1 observer: a run_nodes row + a `node` event at every transition; on a
    terminal state the stage's text becomes a markdown block and an artifact rescan
    registers new files as run_artifacts rows + `file` blocks (A11 tree-diff attribution —
    sound under the profile lock, PRD §2.1)."""
    from ... import artifacts as artifacts_mod

    tree_before = artifacts_mod.snapshot_tree(profile_root)

    async def _on_node(node_id: str, state: str, entry: dict) -> None:
        nonlocal tree_before
        wire_state = await _persist_node(pool, workspace_id, run_id, node_id, entry)
        publish_run_event(
            workspace_id,
            run_id,
            "node",
            {"run_id": run_id, "node_id": node_id, "state": wire_state, "ts": _utc_now()},
        )
        if wire_state not in ("completed", "failed", "skipped"):
            return
        text = entry.get("text") or ""
        if text:
            block = {
                "id": f"node-{node_id}",
                "type": "markdown",
                "props": {"text": text},
                "fallback_text": text[:2000],
            }
            await _upsert_block(pool, workspace_id, run_id, block["id"], node_id, block)
            publish_run_event(
                workspace_id,
                run_id,
                "content",
                {"run_id": run_id, "node_id": node_id, "mode": "replace", "block": block},
            )
        tree_after = artifacts_mod.snapshot_tree(profile_root)
        found = artifacts_mod.diff_new_artifacts(
            profile_root, profile_name, tree_before, tree_after
        )
        tree_before = tree_after
        for art in found:
            async with workspace_scope(pool, workspace_id) as conn:
                artifact_id = await conn.fetchval(
                    """INSERT INTO run_artifacts(run_id, workspace_id, rel_path, name,
                                                 size_bytes, media_type, sha256, node_id)
                       VALUES($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8)
                       ON CONFLICT (run_id, rel_path) DO UPDATE
                         SET size_bytes = EXCLUDED.size_bytes,
                             sha256 = EXCLUDED.sha256,
                             node_id = EXCLUDED.node_id
                       RETURNING id::text""",
                    run_id,
                    workspace_id,
                    art.rel_path,
                    art.name,
                    art.size_bytes,
                    art.media_type,
                    art.sha256,
                    node_id,
                )
            block = _file_block(artifact_id, art)
            await _upsert_block(pool, workspace_id, run_id, block["id"], node_id, block)
            publish_run_event(
                workspace_id,
                run_id,
                "content",
                {"run_id": run_id, "node_id": node_id, "mode": "replace", "block": block},
            )

    return _on_node


def _gate_draft_content(gate_actions, cfg, profile_name: str, gated: dict, *, run_id, enroll):
    """Resolve the pending content for whichever gate just paused, which kind of draft (if
    any) produced it, and that draft's path.

    Returns ``(pending_content, draft_path, draft_kind)`` — ``draft_kind`` is ``"plan"``,
    ``"enroll"``, or ``None`` (a gate with no draft mechanism of its own — outreach,
    reply/triage, format-plan, capture, ...; A11 makes these pause too, so a stub is
    expected here, not an error). An enrollment gate (``enroll``) reads only THIS run's
    draft (client issue #245) and raises if it is missing or malformed: a PII-egress gate
    with nothing valid to approve fails the run at the pause instead of showing a stub.
    """
    found = gate_actions.gate_draft(cfg, profile_name, run_id=run_id, enroll=enroll)
    if enroll and found is None:
        raise gate_actions.EnrollDraftError(
            f"{gated.get('name')!r} wrote no enrollment draft for this run ({run_id})"
        )
    draft_path, draft_kind = found if found is not None else (None, None)
    pending_content = (
        draft_path.read_text(encoding="utf-8")
        if draft_path is not None
        else json.dumps({"node": gated.get("name"), "note": "awaiting approval"})
    )
    if draft_kind == "enroll":
        gate_actions.parse_enroll_draft(pending_content, source=draft_path.name)
    return pending_content, draft_path, draft_kind


def _promote_gate_draft(
    gate_actions,
    cfg,
    profile_name: str,
    draft_kind: str | None,
    edited: str | None,
    pending_content: str,
    *,
    draft_path=None,
    raw_edit_allowed: bool = False,
) -> tuple[dict | None, str | None]:
    """Promote the approved draft, if this gate's kind has one to promote.

    Thin wrapper over :func:`agent.gate_actions.promote_gate_draft` (shared with
    ``agent/__main__.py``'s CLI gate-decision verb so both callers resolve a gate
    identically); kept here so callers that already hold a ``gate_actions`` reference
    (the module is imported locally in ``_execute_pack_run``) don't need a second import.

    An enroll gate promotes the bytes the operator APPROVED (``pending_content``, or their
    edit) — never a re-read of "newest draft in the profile". The profile lock is released
    while a run waits at its gate, so another run in the same profile can write a newer
    draft meanwhile; re-reading would enroll that run's list under this approval (client
    issue #240).

    Edited bytes at a gate with no draft — and no own ``publish`` effect, which consumes
    them directly — have nowhere to go, so they fail the run rather than being dropped.
    ``POST /gate`` refuses these up front (``decisions.refuses_edit``); this is the backstop
    for a decision recorded before the durable gate row existed.
    """
    if edited is not None and draft_kind is None and not raw_edit_allowed:
        return None, (
            "this gate has no editable draft — edited content was not applied; "
            "approve or reject it instead"
        )
    approved_bytes = edited if edited is not None or draft_kind != "enroll" else pending_content
    return gate_actions.promote_gate_draft(
        cfg, profile_name, draft_kind, approved_bytes, draft_path=draft_path
    )


def _dispatch_target(runner, gated_node_id: str) -> tuple[str | None, str]:
    """:func:`.gate_kinds.dispatch_target` over this runner's graph — the fake executor
    resolves its gates through the same rule."""
    return dispatch_target(runner.graph.nodes, gated_node_id)


async def _dispatch_gate2(
    runner,
    cfg,
    profile_name: str,
    *,
    pool,
    workspace_id: str,
    gated_node_id: str,
    edited: str | None,
    pending_content: str,
    has_draft: bool,
    draft_path=None,
    enroll_draft: dict | None,
    dry_run: bool,
) -> tuple[RunFailure | None, tuple[str, str] | None]:
    """Gate 2: if the approval dispatches a DECLARED external effect (see
    :func:`_dispatch_target`), dispatch it — in Python, to a destination/API pinned
    server-side. Driven off the DECLARATION, not the node's name, and off the declared
    VALUE too — A10's publish and A11's email_enroll are both dispatch-only effects, but
    they route to different Python dispatchers, so a plain truthy check (as before A11)
    would wrongly send an approved enrollment through the LinkedIn publisher or vice
    versa. dry_run never dispatches either kind.

    Returns ``(failure, dispatched)``: ``failure`` is set when the run must fail;
    ``dispatched`` is ``(successor_node_id, operator_line)`` when a SUCCESSOR node was
    actually dispatched, so the caller can mark it completed rather than let the runner
    skip it.
    """
    effect, target_id = _dispatch_target(runner, gated_node_id)
    if effect is None:
        return None, None

    if effect == "email_enroll":
        if enroll_draft is None:
            # No enroll draft was promoted at this gate (the brain never wrote one, or it
            # did not parse — that path already fails the run on its own). Enrolling nothing
            # while reporting success would be a false-success; fail closed instead.
            return RunFailure(
                "draft_invalid",
                f"{target_id!r} needs an approved enrollment draft, found none — "
                "the sequence node's enroll-draft could not be promoted",
            ), None
        outcome = await dispatch_backend_email_enroll(
            cfg,
            profile_name,
            pool=pool,
            workspace_id=workspace_id,
            draft=enroll_draft,
            dry_run=dry_run,
        )
        failure = enroll_refusal(outcome)
        if failure is not None or not outcome.ok:
            return failure, None  # dry_run: nothing sent, the runner skips the node as before
        from agent import gate_actions

        gate_actions.clear_enroll_draft(draft_path)
        if target_id == gated_node_id:
            return None, None
        return None, (target_id, outcome.operator_line())

    if effect != "publish":
        # loader.validate_node_semantics's unsafe_gate rule refuses any external_effect
        # outside _ALLOWED_EXTERNAL_EFFECTS at load time — reaching here means the loaded
        # graph disagrees with that validation. Fail loudly rather than silently skip a
        # real dispatch for a kind nobody wrote a branch for.
        return RunFailure(
            "internal_error",
            f"gated node {gated_node_id!r} declares unrecognized external_effect {effect!r}",
        ), None

    if edited is None and not has_draft:
        # No plan draft exists for this node (it isn't a "plan" gate) and the operator
        # approved without submitting edited_content — the only content of record is the
        # `{"node": ..., "note": "awaiting approval"}` JSON stub. Dispatching THAT would
        # publish stub bytes, not a real post. Refuse rather than post placeholder text.
        return RunFailure(
            "publish_draft_missing",
            "publish gate has no draft content to approve — "
            "submit edited_content with the post text",
        ), None
    approved_bytes = edited if edited is not None else pending_content
    outcome = await dispatch_backend_publish(
        cfg,
        profile_name,
        pool=pool,
        workspace_id=workspace_id,
        content=approved_bytes,
        dry_run=dry_run,
    )
    return publish_refusal(outcome), None


async def _complete_approved_gate(
    pool,
    workspace_id: str,
    run_id: str,
    runner,
    manifest: dict,
    gated: dict,
    dispatched: tuple[str, str] | None,
) -> None:
    """Flip the approved node — and a successor Gate 2 dispatched inline, if any — to ok,
    then mirror each flip into run_nodes + a node event (the runner's observer never sees
    these transitions — they happened outside the runner). A dispatched successor also gets
    a markdown block carrying the dispatch outcome line, so a client has evidence of it."""
    gated["status"] = "ok"
    flipped = [(gated.get("name", ""), gated)]
    if dispatched is not None:
        # Marked ok so the resumed runner treats it as complete instead of skipping it.
        succ_id, _line = dispatched
        succ = next((s for s in manifest["stages"] if s.get("name") == succ_id), None)
        if succ is None:
            succ = {"name": succ_id, "status": "ok"}
            manifest["stages"].append(succ)
        else:
            succ["status"] = "ok"
        flipped.append((succ_id, succ))
    runner.ledgers.write_run_manifest(manifest)
    for node_id, entry in flipped:
        await _persist_node(pool, workspace_id, run_id, node_id, entry)
        publish_run_event(
            workspace_id,
            run_id,
            "node",
            {"run_id": run_id, "node_id": node_id, "state": "completed", "ts": _utc_now()},
        )
    if dispatched is not None:
        succ_id, line = dispatched
        block = {
            "id": f"node-{succ_id}",
            "type": "markdown",
            "props": {"text": line},
            "fallback_text": line,
        }
        await _upsert_block(pool, workspace_id, run_id, block["id"], succ_id, block)
        publish_run_event(
            workspace_id,
            run_id,
            "content",
            {"run_id": run_id, "node_id": succ_id, "mode": "replace", "block": block},
        )


async def _fail_from_outcome(
    pool,
    workspace_id: str,
    run_id: str,
    manifest: dict,
    verdicts: list[bool],
    cancelled: list[bool],
) -> None:
    """The runner returned ``FAILED`` — distinguish RL-02's cancel-triggered stop from a
    genuine node/budget failure before deciding whether to write one.

    ``cancelled[-1:] == [True]`` means ``_budget_guard``'s predicate returned ``False``
    because THIS run went ``canceled`` mid-dispatch, not because a node genuinely failed
    or the cost cap was exhausted. ``decisions.cancel()`` already wrote the terminal row
    (and closed any open gate) — calling :func:`_fail_run` here would try to record the
    WRONG story (``cost_cap_reached``) even though the RL-03 terminal guard would silently
    absorb the write. Leave the row exactly as ``cancel()`` left it.
    """
    if cancelled[-1:] == [True]:
        _cancelled_runs.discard(run_id)
        return
    first_error = next(
        (
            s.get("error") or f"stage {s.get('name')} failed"
            for s in manifest["stages"]
            if s.get("status") == "failed"
        ),
        "pack run failed",
    )
    code = "cost_cap_reached" if verdicts[-1:] == [False] else "node_failed"
    await _fail_run(pool, workspace_id, run_id, first_error, error_code=code)


async def _execute_pack_run(
    pool,
    repo_root,
    workspace_id: str,
    run_id: str,
    profile_name: str,
    pack: str,
    variant: str,
    inputs: dict[str, str],
    *,
    entitlement: Entitlement | str,
    agent_id: str | None = None,
    agent_budget_usd: float | None = None,
    language: str | None = None,
    dry_run: bool = False,
) -> None:
    """Background task for pack mode: drive the graph runner with gates held OUTSIDE it.

    Composition (the backend twin of agent/__main__.py's VPS root): workspace-scoped
    Config → resolve_variant (loader + tenant merge, re-validated) → engine graph →
    pack executor (Postgres cost sink + pack-reachability ∩ entitlement skill scope,
    gtm_core.packs.reachability.entitled_skills_for_profile) → PipelineRunner with a
    Postgres §R2 per-batch budget predicate.

    Gate 1 is return-and-resume: the runner RETURNS on awaiting_approval (profile
    lock released — never held across a human wait), the operator decides via
    POST /gate exactly as in prompt mode, and approval promotes the plan draft
    deterministically (agent/gate_actions.py) before flipping the gated node and
    re-entering the runner — the frontier continues downstream. This shape is the
    A5 durable-gate shape; A5 swaps the in-memory event for a run_gates row.
    """
    try:
        if run_id in _cancelled_runs:
            _cancelled_runs.discard(run_id)
            return

        # RL-13/ST-06: a boot reconcile (reconcile.py) or a lease reclaim of a run
        # already sitting at awaiting_approval (queue.py's dispatch_claimed, when
        # claim_next_run's prev_status was awaiting_approval) is a RESUME of a still-open
        # gate wait, not a fresh start. The reserve/start pair below is this function's
        # ONE-TIME admission (a §R2 cap check with no user action behind it, and the
        # running transition + started_at) — re-running it on a resume would (a) fail a
        # run legitimately parked at a gate with "monthly cost cap reached" for a cap
        # that has nothing to do with it, and (b) flip the row back to 'running',
        # overwriting started_at and lying to every client polling/streaming it. Skipping
        # it changes nothing else: the runner loop below re-enters its gate-holding
        # branch exactly as it does on every reclaim already (unchanged), and the §R2
        # per-batch _budget_guard still runs before every dispatch batch either way.
        resuming = await run_status(pool, workspace_id, run_id) == "awaiting_approval"

        if not resuming:
            if not await _reserve_or_deny(pool, workspace_id, run_id):
                await _fail_run(
                    pool, workspace_id, run_id, _CAP_REACHED, error_code="cost_cap_reached"
                )
                return
            await start_run(pool, workspace_id, run_id)

        import dataclasses

        from agent import gate_actions
        from agent.config import Config
        from agent.packs import make_executor_from_pack, pack_graph_to_engine_graph
        from agent.pipeline import AWAITING_APPROVAL, PipelineRunner, terminal_status
        from gtm_core.packs.reachability import entitled_skills_for_profile

        from ...pack_catalog import resolve_variant
        from ...services.integrations import get_workspace_credentials
        from ...session import _workspace_scoped_config, make_pg_usage_sink

        base_cfg = Config.from_env(repo_root=repo_root)
        creds = await get_workspace_credentials(pool, workspace_id)
        cfg = _workspace_scoped_config(base_cfg, workspace_id, repo_root, credentials=creds)
        resolved = resolve_variant(repo_root, cfg.profiles_root, profile_name, pack, variant)

        # v1 ask-inputs: appended to each prompted node as a suffix line. No inputs ⇒
        # prompts stay byte-identical to the pack file (the golden-trajectory
        # contract); the values are also in the runs.prompt audit column.
        pack_graph = resolved.graph
        if inputs:
            suffix = "\n\nRun inputs (operator-provided): " + json.dumps(inputs, sort_keys=True)
            pack_graph = dataclasses.replace(
                pack_graph,
                nodes=tuple(
                    dataclasses.replace(n, prompt=n.prompt + suffix) if n.prompt else n
                    for n in pack_graph.nodes
                ),
            )

        allowed_skills = entitled_skills_for_profile(
            cfg.profiles_root, profile_name, repo_root / "packs", entitlement
        )
        executor = make_executor_from_pack(
            cfg,
            profile_name,
            pack_graph,
            usage_sink=make_pg_usage_sink(pool, workspace_id, run_id, agent_id=agent_id),
            allowed_skills=allowed_skills,
            language=language,
        )

        # The observer needs the profile's file tree to attribute new artifacts.
        from gtm_core.paths import _safe_segment

        _safe_segment(profile_name, "profile")  # B1 defense-in-depth (also at create_run)
        budget_ok, verdicts, cancelled = _budget_guard(
            pool, workspace_id, run_id, agent_id, agent_budget_usd
        )
        runner = PipelineRunner(
            cfg,
            profile_name,
            graph=pack_graph_to_engine_graph(pack_graph),
            budget_ok=budget_ok,
            on_node=_node_observer(
                pool, workspace_id, run_id, profile_name, cfg.content_root / profile_name
            ),
        )

        manifest: dict | None = None
        while True:
            manifest = await runner.run(run_id, "backend", executor, manifest=manifest)
            outcome = terminal_status(manifest)

            if outcome == "failed":
                await _fail_from_outcome(pool, workspace_id, run_id, manifest, verdicts, cancelled)
                return

            if outcome == "ok":
                if run_id in _cancelled_runs:
                    _cancelled_runs.discard(run_id)
                    return
                output = json.dumps(
                    {"pack": pack, "variant": variant, "stages": manifest["stages"]},
                    ensure_ascii=False,
                )
                await complete_run(pool, workspace_id, run_id, output)
                return

            # awaiting_approval — Gate 1 (or A11's second kind). The content of record is
            # whichever draft this gate produced — a plan draft, an enroll draft, or (a
            # gate with neither, e.g. outreach/reply/format-plan/capture) a JSON stub. A11:
            # since agent/pipeline_executor.py now pauses on ANY pack-declared gate=true
            # node (not only ones that emit ⟦GATE:plan⟧), most gates reaching this branch
            # carry no draft file at all — that is expected, not an error; approve simply
            # unblocks them.
            gated = next(s for s in manifest["stages"] if s.get("status") == AWAITING_APPROVAL)
            gated_node_id = gated.get("name", "")
            effect = _dispatch_target(runner, gated_node_id)[0]
            pending_content, draft_path, draft_kind = _gate_draft_content(
                gate_actions,
                cfg,
                profile_name,
                gated,
                run_id=run_id,
                enroll=effect == "email_enroll",
            )
            has_draft = draft_path is not None
            # The run_gates.gate label (V022 CHECK: plan | publish | email_enroll | review) —
            # "publish" never appears here (a node with external_effect="publish" always
            # short-circuits to SKIPPED before reaching AWAITING_APPROVAL, so it can never be
            # `gated`); "review" is the catch-all for a gate with no draft mechanism of its own.
            gate_kind = pack_gate_kind(draft_kind)

            # Durable: the run_gates row persists BOTH the wait and the decision, so a
            # decision posted while this runner was down is claimed on resume (and, being
            # already recorded, does not re-notify). See lifecycle.hold_gate.
            decision = await hold_gate(
                pool,
                workspace_id,
                run_id,
                sentinel=_GATE_PLAN_SENTINEL,
                gate=gate_kind,
                pending_content=pending_content,
                node_id=gated_node_id,
                durable=True,
            )
            if decision in (CANCELLED, TIMED_OUT):
                return
            if not approved(decision):
                # Only pack mode has a draft to discard — a rejected gate must not survive
                # to be promoted/dispatched by the next run.
                if draft_kind == "plan":
                    gate_actions.discard_plan_draft(cfg, profile_name)
                elif draft_kind == "enroll":
                    gate_actions.discard_enroll_draft(cfg, profile_name, path=draft_path)
                await reject_run(pool, workspace_id, run_id)
                return

            # Approved — re-gate before spending more (H6), same as prompt mode.
            if not await _reserve_or_deny(pool, workspace_id, run_id):
                await _fail_run(
                    pool, workspace_id, run_id, _CAP_REACHED, error_code="cost_cap_reached"
                )
                return

            edited = decision.get("edited_content")
            enroll_draft, promote_error = _promote_gate_draft(
                gate_actions,
                cfg,
                profile_name,
                draft_kind,
                edited,
                pending_content,
                draft_path=draft_path,
                raw_edit_allowed=effect == "publish",
            )
            if promote_error is not None:
                await _fail_run(
                    pool, workspace_id, run_id, promote_error, error_code="draft_invalid"
                )
                return
            if edited is not None:
                await rewrite_pending_content(pool, workspace_id, run_id, edited)

            failure, dispatched = await _dispatch_gate2(
                runner,
                cfg,
                profile_name,
                pool=pool,
                workspace_id=workspace_id,
                gated_node_id=gated_node_id,
                edited=edited,
                pending_content=pending_content,
                has_draft=has_draft,
                draft_path=draft_path,
                enroll_draft=enroll_draft,
                dry_run=dry_run,
            )
            if failure is not None:
                await _fail_run(pool, workspace_id, run_id, failure.error, error_code=failure.code)
                return

            # Flip the gated node to complete and resume — the runner recomputes the
            # frontier from the mutated manifest and continues downstream. Mirror the
            # flip into run_nodes + a node event (the runner's observer never sees this
            # transition — it happened here, outside the runner).
            await _complete_approved_gate(
                pool, workspace_id, run_id, runner, manifest, gated, dispatched
            )
            await resume_run(pool, workspace_id, run_id)
    except Exception as exc:  # noqa: BLE001
        try:
            await _fail_run(pool, workspace_id, run_id, str(exc), error_code="internal_error")
        except Exception:  # noqa: BLE001
            pass  # nosec B110 — intentional best-effort swallow
