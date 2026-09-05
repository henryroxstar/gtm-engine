"""Pack-mode run lifecycle (``_execute_pack_run``): drive the graph runner with gates held
OUTSIDE it (return-and-resume), durable gate rows, per-node persistence + events, artifact
attribution, and the A10 Gate-2 publish dispatch."""

from __future__ import annotations

import json

from gtm_core.capabilities import Entitlement
from gtm_core.metering import acheck_budget

from ...database import workspace_scope
from ...publish_dispatch import dispatch_backend_publish, publish_gated_node
from .budget import _reserve_or_deny
from .events import _publish_event, _utc_now
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
from .persistence import _fail_run, _file_block, _persist_node, _upsert_block
from .state import _cancelled_runs

# The per-workspace concurrency slot is freed by the task done-callback set in
# create_run (_track_run) — guaranteed on success, error, or cancellation.


_GATE_PLAN_SENTINEL = "⟦GATE:plan⟧"
_CAP_REACHED = "monthly cost cap reached"


def _budget_guard(pool, workspace_id: str, agent_id: str | None, agent_budget_usd: float | None):
    """The §R2 per-batch predicate the runner calls before EVERY dispatch batch."""

    async def _budget_ok() -> bool:
        # Guard against the backend's ledger of record (Postgres cost_records) — the
        # runner's default JSONL read would see $0 here. A4: the per-agent budget can only
        # NARROW the workspace verdict (effective cap = min(workspace cap, agent budget)),
        # re-derived at every spend check so an entitlement downgrade bites immediately.
        from ...agents import acheck_agent_budget

        async with workspace_scope(pool, workspace_id) as conn:
            if not await acheck_budget(
                pool, workspace_id, table="cost_records", conn=conn, fail_closed=True
            ):
                return False
            return await acheck_agent_budget(conn, workspace_id, agent_id, agent_budget_usd)

    return _budget_ok


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
        _publish_event(
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
            _publish_event(
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
            _publish_event(
                run_id,
                "content",
                {"run_id": run_id, "node_id": node_id, "mode": "replace", "block": block},
            )

    return _on_node


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
    dry_run: bool,
) -> str | None:
    """A10 Gate 2: if the approved node DECLARES an external effect, the approval dispatches
    it — in Python, with the exact approved bytes, to a destination pinned server-side.
    Driven off the declaration, not the node's name; dry_run never dispatches.

    Returns an error string when the run must fail, else None (dispatched, or nothing to do).
    """
    if not publish_gated_node(runner.graph, gated_node_id):
        return None
    if edited is None and not has_draft:
        # No plan draft exists for this node (it isn't a "plan" gate) and the operator
        # approved without submitting edited_content — the only content of record is the
        # `{"node": ..., "note": "awaiting approval"}` JSON stub. Dispatching THAT would
        # publish stub bytes, not a real post. Refuse rather than post placeholder text.
        return (
            "publish gate has no draft content to approve — "
            "submit edited_content with the post text"
        )
    approved_bytes = edited if edited is not None else pending_content
    outcome = await dispatch_backend_publish(
        cfg,
        profile_name,
        pool=pool,
        workspace_id=workspace_id,
        content=approved_bytes,
        dry_run=dry_run,
    )
    if outcome is None:
        # No enabled destination for this workspace (or dispatch_backend_publish swallowed an
        # internal exception) — nothing was sent. Without this the gated node still flipped to
        # "complete", marking the run as if the post went out even though dispatch never happened.
        return "no publish destination configured for this workspace — not published"
    # Any non-success outcome fails the run, EXCEPT: dry_run (a structurally intentional no-op —
    # see agent/publish_dispatch.py) and a "duplicate" publish (the A5 gate-decision-replay path
    # re-dispatching bytes already sent before a restart — idempotency, not failure). Every other
    # status — hash_mismatch, disclosure_missing, and every LinkedInPublisher failure mode
    # collapsed under "publish_failed" (disabled/schedule_disabled/misconfigured/invalid/
    # rate_limited/error) — must not silently read as a successful publish.
    already_published = outcome.result is not None and outcome.result.status == "duplicate"
    if outcome.status != "dry_run" and not outcome.ok and not already_published:
        return outcome.operator_line()
    return None


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

        if not await _reserve_or_deny(pool, workspace_id, run_id):
            await _fail_run(pool, workspace_id, run_id, _CAP_REACHED)
            return
        await start_run(pool, workspace_id, run_id)

        import dataclasses

        from agent import gate_actions
        from agent.config import Config
        from agent.packs import make_executor_from_pack, pack_graph_to_engine_graph
        from agent.pipeline import AWAITING_APPROVAL, PipelineRunner, terminal_status
        from gtm_core.packs.reachability import entitled_skills_for_profile

        from ...pack_catalog import resolve_variant
        from ...session import _workspace_scoped_config, make_pg_usage_sink

        base_cfg = Config.from_env(repo_root=repo_root)
        cfg = _workspace_scoped_config(base_cfg, workspace_id, repo_root)
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
        runner = PipelineRunner(
            cfg,
            profile_name,
            graph=pack_graph_to_engine_graph(pack_graph),
            budget_ok=_budget_guard(pool, workspace_id, agent_id, agent_budget_usd),
            on_node=_node_observer(
                pool, workspace_id, run_id, profile_name, cfg.content_root / profile_name
            ),
        )

        manifest: dict | None = None
        while True:
            manifest = await runner.run(run_id, "backend", executor, manifest=manifest)
            outcome = terminal_status(manifest)

            if outcome == "failed":
                first_error = next(
                    (
                        s.get("error") or f"stage {s.get('name')} failed"
                        for s in manifest["stages"]
                        if s.get("status") == "failed"
                    ),
                    "pack run failed",
                )
                await _fail_run(pool, workspace_id, run_id, first_error)
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

            # awaiting_approval — Gate 1. The content of record is the pending plan
            # draft (the exact bytes promotion will consume); a gated node without a
            # draft pauses on a JSON stub and approve simply unblocks it.
            gated = next(s for s in manifest["stages"] if s.get("status") == AWAITING_APPROVAL)
            draft_path = gate_actions.latest_plan_draft(cfg, profile_name)
            has_draft = draft_path is not None
            pending_content = (
                draft_path.read_text(encoding="utf-8")
                if has_draft
                else json.dumps({"node": gated.get("name"), "note": "awaiting approval"})
            )

            gated_node_id = gated.get("name", "")
            # Durable: the run_gates row persists BOTH the wait and the decision, so a
            # decision posted while this runner was down is claimed on resume (and, being
            # already recorded, does not re-notify). See lifecycle.hold_gate.
            decision = await hold_gate(
                pool,
                workspace_id,
                run_id,
                sentinel=_GATE_PLAN_SENTINEL,
                gate="plan",
                pending_content=pending_content,
                node_id=gated_node_id,
                durable=True,
            )
            if decision in (CANCELLED, TIMED_OUT):
                return
            if not approved(decision):
                # Only pack mode has a draft to discard — a rejected plan must not survive
                # to be promoted by the next run.
                gate_actions.discard_plan_draft(cfg, profile_name)
                await reject_run(pool, workspace_id, run_id)
                return

            # Approved — re-gate before spending more (H6), same as prompt mode.
            if not await _reserve_or_deny(pool, workspace_id, run_id):
                await _fail_run(pool, workspace_id, run_id, _CAP_REACHED)
                return

            edited = decision.get("edited_content")
            if has_draft:
                try:
                    gate_actions.promote_plan_draft(cfg, profile_name, edited_content=edited)
                except gate_actions.PlanDraftError as exc:
                    await _fail_run(pool, workspace_id, run_id, str(exc))
                    return
            if edited is not None:
                await rewrite_pending_content(pool, workspace_id, run_id, edited)

            publish_error = await _dispatch_gate2(
                runner,
                cfg,
                profile_name,
                pool=pool,
                workspace_id=workspace_id,
                gated_node_id=gated_node_id,
                edited=edited,
                pending_content=pending_content,
                has_draft=has_draft,
                dry_run=dry_run,
            )
            if publish_error is not None:
                await _fail_run(pool, workspace_id, run_id, publish_error)
                return

            # Flip the gated node to complete and resume — the runner recomputes the
            # frontier from the mutated manifest and continues downstream. Mirror the
            # flip into run_nodes + a node event (the runner's observer never sees this
            # transition — it happened here, outside the runner).
            gated["status"] = "ok"
            runner.ledgers.write_run_manifest(manifest)
            await _persist_node(pool, workspace_id, run_id, gated_node_id, gated)
            _publish_event(
                run_id,
                "node",
                {
                    "run_id": run_id,
                    "node_id": gated_node_id,
                    "state": "completed",
                    "ts": _utc_now(),
                },
            )
            await resume_run(pool, workspace_id, run_id)
    except Exception as exc:  # noqa: BLE001
        try:
            await _fail_run(pool, workspace_id, run_id, str(exc))
        except Exception:  # noqa: BLE001
            pass  # nosec B110 — intentional best-effort swallow
