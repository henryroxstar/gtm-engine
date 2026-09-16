"""H2: real pack graphs driven end-to-end through the REAL PipelineRunner, with a
fake executor keyed by ``node.skill`` returning canned outcomes — no SDK, no brain
call, deterministic and fast.

The gap this closes: every existing E2E proof of the runner loop (lock/persist/
fan-out/gate/resume semantics) uses a hand-built SYNTHETIC ``Graph`` (diamond a→b,c→d
shapes in tests/agent/test_graph_runner.py and tests/contracts/test_pack_generality.py)
— none of them runs an actual shipped pack TOML through the runner end-to-end. This
file retrofits that proof onto the real packs, using the same idioms those files
already established (the ``_executor`` closure, the ``_capturing_write`` manifest-
snapshot trick, the ``_counting_lock``/``vps_budget_ok`` monkeypatches) rather than
inventing new ones.

``run_pack`` is the reusable harness: load a pack graph, run it through
``PipelineRunner`` with a fake skill-keyed executor, optionally capture every
persisted manifest snapshot and the dispatch order. Future phases (10/11 — the
repurpose and restyle lanes) extend the parametrized sections below with their own
new pack variants; nothing here is creator-pack-specific except the actual test
bodies that assert on ITS particular shape (which gates pause, which nodes fan out).
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import dataclasses
from pathlib import Path

import pytest

from agent.config import Config
from agent.packs import load_engine_graph
from agent.pipeline import AWAITING_APPROVAL, OK, PENDING, PipelineRunner, StageOutcome

REPO = Path(__file__).resolve().parents[2]
PROFILE = "example"

CREATOR_GRAPH = REPO / "packs" / "creator" / "graphs" / "short-form-video.toml"
REPURPOSE_GRAPH = REPO / "packs" / "creator" / "graphs" / "repurpose-clips.toml"
RESTYLE_GRAPH = REPO / "packs" / "creator" / "graphs" / "restyle-shorts.toml"
CROSS_MODAL_GRAPH = REPO / "packs" / "creator" / "graphs" / "cross-modal-campaign.toml"
PROSPECTING_GRAPH = REPO / "packs" / "prospecting" / "graphs" / "prospect-outreach.toml"
PLANNING_GRAPH = REPO / "packs" / "planning" / "graphs" / "planning.toml"


@pytest.fixture()
def cfg(tmp_path):
    base = Config.from_env(repo_root=REPO)
    return dataclasses.replace(base, content_root=tmp_path / "content")


def run_pack(
    cfg,
    pack_path: Path,
    *,
    run_id: str,
    outcomes_by_skill: dict | None = None,
    snapshots: list | None = None,
    ran: list | None = None,
    manifest: dict | None = None,
):
    """Load ``pack_path`` and run it through the REAL ``PipelineRunner``.

    The fake executor is keyed by ``node.skill`` (falling back to the node id, then
    ``StageOutcome(status=OK)``) — so a test only needs to say "the render skill
    returns X", not "node render-vertical returns X and render-feed also returns X"
    even though the creator pack's two render nodes share one skill.

    Returns ``(pack, engine_graph, final_manifest)``.
    """
    pack, engine = load_engine_graph(pack_path)
    skill_by_node = {n.id: n.skill for n in pack.nodes}
    outcomes_by_skill = outcomes_by_skill or {}

    runner = PipelineRunner(cfg, PROFILE, graph=engine)
    if snapshots is not None:
        real_write = runner.ledgers.write_run_manifest

        def _capturing_write(m):
            snapshots.append(copy.deepcopy(m))
            return real_write(m)

        runner.ledgers.write_run_manifest = _capturing_write

    async def _executor(stage: str, m: dict) -> StageOutcome:
        if ran is not None:
            ran.append(stage)
        skill = skill_by_node.get(stage)
        outcome = outcomes_by_skill.get(skill, outcomes_by_skill.get(stage))
        return outcome or StageOutcome(status=OK)

    final = asyncio.run(runner.run(run_id, "cron", _executor, manifest=manifest))
    return pack, engine, final


def _approve_gate(manifest: dict, node_id: str) -> dict:
    """Simulate an operator approving a gated node: flip its status to OK in a COPY
    of the manifest, matching the real approve-flow (backend/routers/runs.py's
    ``gated["status"] = "ok"``) — re-running with the status left at
    AWAITING_APPROVAL would just re-invoke the executor for that same node, since
    the runner's completeness check treats AWAITING_APPROVAL as not-complete."""
    m = copy.deepcopy(manifest)
    entry = next(s for s in m["stages"] if s["name"] == node_id)
    entry["status"] = OK
    return m


# ── creator pack: gates pause, fan-out dispatches concurrently, resume completes ──


def test_creator_pack_gates_pause_the_run_and_leave_downstream_pending(cfg):
    ran: list[str] = []
    _pack, _engine, manifest = run_pack(
        cfg,
        CREATOR_GRAPH,
        run_id="e2e-creator-1",
        outcomes_by_skill={"content-plan": StageOutcome(status=AWAITING_APPROVAL)},
        ran=ran,
    )
    assert ran == ["radar", "plan"]
    by_name = {s["name"]: s["status"] for s in manifest["stages"]}
    assert by_name["plan"] == AWAITING_APPROVAL
    for downstream in ("brief", "script", "render-vertical", "render-feed", "score", "publish"):
        assert by_name[downstream] == PENDING


def test_creator_pack_resumes_past_an_approved_gate(cfg):
    ran1: list[str] = []
    _pack, _engine, m1 = run_pack(
        cfg,
        CREATOR_GRAPH,
        run_id="e2e-creator-2",
        outcomes_by_skill={"content-plan": StageOutcome(status=AWAITING_APPROVAL)},
        ran=ran1,
    )
    approved = _approve_gate(m1, "plan")

    ran2: list[str] = []
    _pack, _engine, m2 = run_pack(
        cfg,
        CREATOR_GRAPH,
        run_id="e2e-creator-2",
        outcomes_by_skill={"content-publish": StageOutcome(status=AWAITING_APPROVAL)},
        ran=ran2,
        manifest=approved,
    )
    # plan was NOT re-invoked (it was pre-approved); the run proceeded through the
    # unmodified engine to script/render/score, and stopped again at the publish gate.
    assert "plan" not in ran2
    assert set(ran2) >= {"script", "render-vertical", "render-feed", "score", "publish"}
    by_name = {s["name"]: s["status"] for s in m2["stages"]}
    assert by_name["publish"] == AWAITING_APPROVAL


def test_creator_pack_render_fan_out_dispatches_concurrently(cfg):
    """Retrofits the synthetic-graph concurrency proof
    (test_pack_generality.test_fan_out_batch_shows_concurrent_running_nodes_in_one_persist)
    onto the REAL creator pack: render-vertical and render-feed must appear
    RUNNING together in at least one persisted snapshot."""
    snapshots: list[dict] = []
    run_pack(
        cfg,
        CREATOR_GRAPH,
        run_id="e2e-creator-3",
        outcomes_by_skill={
            "content-plan": StageOutcome(status=OK),
            "content-publish": StageOutcome(status=AWAITING_APPROVAL),
        },
        snapshots=snapshots,
    )
    both_running = [
        snap
        for snap in snapshots
        if {s["name"]: s["status"] for s in snap["stages"]}.get("render-vertical") == "running"
        and {s["name"]: s["status"] for s in snap["stages"]}.get("render-feed") == "running"
    ]
    assert both_running, "no persisted snapshot showed both render nodes RUNNING at once"


def test_creator_pack_run_holds_exactly_one_profile_lock_through_the_fan_out(cfg, monkeypatch):
    lock_calls = {"n": 0}

    @contextlib.contextmanager
    def _counting_lock(content_root, profile, *, blocking=True):
        lock_calls["n"] += 1
        yield content_root

    monkeypatch.setattr("agent.pipeline.profile_lock", _counting_lock)
    run_pack(
        cfg,
        CREATOR_GRAPH,
        run_id="e2e-creator-4",
        outcomes_by_skill={
            "content-plan": StageOutcome(status=OK),
            "content-publish": StageOutcome(status=AWAITING_APPROVAL),
        },
    )
    assert lock_calls["n"] == 1


def test_creator_pack_budget_guard_fires_before_the_render_batch(cfg, monkeypatch):
    """§R2 on a REAL pack: the guard must abort before the (paid) render fan-out,
    not just once at run start — mirrors the synthetic-graph version in
    test_pack_generality.py, here against the actual money-spending nodes."""
    calls = {"n": 0}

    def _budget_ok(cfg_arg, profile):
        calls["n"] += 1
        # radar, plan, brief, script, storyboard each dispatch as their own single-node batch
        # (brief joined 2026-09-06 as creator-brief's node); the render fan-out is the 6th
        # batch — that's the one that must be refused.
        return calls["n"] <= 5

    monkeypatch.setattr("agent.budget.vps_budget_ok", _budget_ok)
    ran: list[str] = []
    _pack, _engine, manifest = run_pack(
        cfg,
        CREATOR_GRAPH,
        run_id="e2e-creator-5",
        outcomes_by_skill={"content-plan": StageOutcome(status=OK)},
        ran=ran,
    )
    assert "render-vertical" not in ran and "render-feed" not in ran
    by_name = {s["name"]: s["status"] for s in manifest["stages"]}
    assert by_name["render-vertical"] == "failed"
    error = next(s.get("error", "") for s in manifest["stages"] if s["name"] == "render-vertical")
    assert "cost cap" in error


# ── other shipped packs: the harness generalizes past creator's shape ─────────


def test_prospecting_pack_runs_start_to_finish_when_nothing_gates(cfg):
    """One pause, at `sequence` — discovery, drafting and judging must all run uninterrupted
    through the real runner before it, and `outreach` must NOT stop the run."""
    ran: list[str] = []
    _pack, _engine, manifest = run_pack(
        cfg,
        PROSPECTING_GRAPH,
        run_id="e2e-prospect-1",
        outcomes_by_skill={"email-sequence": StageOutcome(status=AWAITING_APPROVAL)},
        ran=ran,
    )
    assert ran == ["prospect", "dossier", "outreach", "quality", "sequence"]
    by_name = {s["name"]: s["status"] for s in manifest["stages"]}
    assert by_name["outreach"] == OK
    assert by_name["sequence"] == AWAITING_APPROVAL


def test_planning_pack_three_roots_dispatch_in_one_batch(cfg):
    """No depends_on between any of the three nodes — the very first frontier must
    be all three, exactly as the pure-frontier test in test_pack_generality.py
    already proves for the pack DATA; here the real runner actually does it."""
    ran: list[str] = []
    _pack, _engine, manifest = run_pack(cfg, PLANNING_GRAPH, run_id="e2e-planning-1", ran=ran)
    assert set(ran) == {"gtm-plan", "account-plan", "event-plan"}
    assert all(s["status"] == OK for s in manifest["stages"])


# ── Phase 10: repurpose-clips lane (same pack, second variant) ────────────────


def test_repurpose_clips_pack_gates_pause_at_plan_and_publish(cfg):
    ran: list[str] = []
    _pack, _engine, manifest = run_pack(
        cfg,
        REPURPOSE_GRAPH,
        run_id="e2e-repurpose-1",
        outcomes_by_skill={"content-plan": StageOutcome(status=AWAITING_APPROVAL)},
        ran=ran,
    )
    # `plan` alone: the radar root was removed 2026-08-20 — footage in hand needs no discovery.
    assert ran == ["plan"]
    by_name = {s["name"]: s["status"] for s in manifest["stages"]}
    assert by_name["plan"] == AWAITING_APPROVAL
    for downstream in ("clip", "score", "publish"):
        assert by_name[downstream] == PENDING


def test_repurpose_clips_pack_resumes_past_an_approved_plan_gate(cfg):
    ran1: list[str] = []
    _pack, _engine, m1 = run_pack(
        cfg,
        REPURPOSE_GRAPH,
        run_id="e2e-repurpose-2",
        outcomes_by_skill={"content-plan": StageOutcome(status=AWAITING_APPROVAL)},
        ran=ran1,
    )
    approved = _approve_gate(m1, "plan")

    ran2: list[str] = []
    _pack, _engine, m2 = run_pack(
        cfg,
        REPURPOSE_GRAPH,
        run_id="e2e-repurpose-2",
        outcomes_by_skill={"content-publish": StageOutcome(status=AWAITING_APPROVAL)},
        ran=ran2,
        manifest=approved,
    )
    assert "plan" not in ran2
    assert ran2 == ["clip", "score", "publish"]  # a straight chain, no fan-out in this variant
    by_name = {s["name"]: s["status"] for s in m2["stages"]}
    assert by_name["publish"] == AWAITING_APPROVAL


def test_repurpose_clips_opens_at_the_plan_gate_not_a_radar_scan():
    """Radar root REMOVED 2026-08-20 — deliberately, reversing this test's original assertion.

    The old shape assumed this lane discovers a source video. It does not: the operator already
    HAS the footage, which is the same reasoning restyle-shorts has always carried. Opening with a
    news scan made the operator sit through a discovery step they never asked for, on the way to
    the cheapest and highest-quality lane in the pack.

    This still guards a real property — the lane must open at the operator gate, not at a scan —
    it just guards the opposite shape now.
    """
    from gtm_core.packs.loader import load_pack_graph

    pack = load_pack_graph(REPURPOSE_GRAPH)
    assert [n.id for n in pack.nodes if not n.depends_on] == ["plan"], (
        "the footage lane must open at the plan gate — nothing precedes the operator here"
    )
    assert pack.node("plan").gate is True


# ── Phase 11: restyle-shorts lane (root-gate shape, no radar) ─────────────────


def test_restyle_shorts_pack_first_frontier_is_the_plan_gate_alone(cfg):
    """No radar node — the graph's very first (and only initial) runnable node is
    `plan` itself, unlike every other lane in this pack which opens with radar."""
    ran: list[str] = []
    _pack, _engine, manifest = run_pack(
        cfg,
        RESTYLE_GRAPH,
        run_id="e2e-restyle-1",
        outcomes_by_skill={"content-plan": StageOutcome(status=AWAITING_APPROVAL)},
        ran=ran,
    )
    assert ran == ["plan"]
    by_name = {s["name"]: s["status"] for s in manifest["stages"]}
    assert by_name["plan"] == AWAITING_APPROVAL
    for downstream in ("restyle", "score", "publish"):
        assert by_name[downstream] == PENDING


def test_restyle_shorts_pack_resumes_past_an_approved_plan_gate(cfg):
    ran1: list[str] = []
    _pack, _engine, m1 = run_pack(
        cfg,
        RESTYLE_GRAPH,
        run_id="e2e-restyle-2",
        outcomes_by_skill={"content-plan": StageOutcome(status=AWAITING_APPROVAL)},
        ran=ran1,
    )
    approved = _approve_gate(m1, "plan")

    ran2: list[str] = []
    _pack, _engine, m2 = run_pack(
        cfg,
        RESTYLE_GRAPH,
        run_id="e2e-restyle-2",
        outcomes_by_skill={"content-publish": StageOutcome(status=AWAITING_APPROVAL)},
        ran=ran2,
        manifest=approved,
    )
    assert "plan" not in ran2
    assert ran2 == ["restyle", "score", "publish"]
    by_name = {s["name"]: s["status"] for s in m2["stages"]}
    assert by_name["publish"] == AWAITING_APPROVAL


def test_restyle_shorts_pack_has_no_radar_node_at_all():
    from gtm_core.packs.loader import load_pack_graph

    pack = load_pack_graph(RESTYLE_GRAPH)
    assert "radar" not in {n.id for n in pack.nodes}
    assert pack.node("plan").depends_on == ()


# ── Grade A+ Phase 12: cross-modal-campaign graph (format-router dispatcher) ──


def test_cross_modal_pack_gates_at_format_plan_and_publish(cfg):
    ran: list[str] = []
    _pack, _engine, manifest = run_pack(
        cfg,
        CROSS_MODAL_GRAPH,
        run_id="e2e-cross-modal-1",
        outcomes_by_skill={"format-router": StageOutcome(status=AWAITING_APPROVAL)},
        ran=ran,
    )
    assert ran == ["radar", "format-plan"]
    by_name = {s["name"]: s["status"] for s in manifest["stages"]}
    assert by_name["format-plan"] == AWAITING_APPROVAL
    for downstream in ("text-studio", "image-studio", "video-script", "publish"):
        assert by_name[downstream] == PENDING


def test_cross_modal_pack_studio_fan_out_dispatches_concurrently(cfg):
    """Once format-plan is approved, the three modality studios must all run in
    one concurrent frontier before the publish gate."""
    snapshots: list[dict] = []
    run_pack(
        cfg,
        CROSS_MODAL_GRAPH,
        run_id="e2e-cross-modal-2",
        outcomes_by_skill={
            "format-router": StageOutcome(status=OK),
            "content-publish": StageOutcome(status=AWAITING_APPROVAL),
        },
        snapshots=snapshots,
    )
    all_running = [
        snap
        for snap in snapshots
        if {s["name"]: s["status"] for s in snap["stages"]}.get("text-studio") == "running"
        and {s["name"]: s["status"] for s in snap["stages"]}.get("image-studio") == "running"
        and {s["name"]: s["status"] for s in snap["stages"]}.get("video-script") == "running"
    ]
    assert all_running, "no persisted snapshot showed all three studios RUNNING at once"


def test_cross_modal_pack_resumes_past_an_approved_plan_gate(cfg):
    ran1: list[str] = []
    _pack, _engine, m1 = run_pack(
        cfg,
        CROSS_MODAL_GRAPH,
        run_id="e2e-cross-modal-3",
        outcomes_by_skill={"format-router": StageOutcome(status=AWAITING_APPROVAL)},
        ran=ran1,
    )
    approved = _approve_gate(m1, "format-plan")

    ran2: list[str] = []
    _pack, _engine, m2 = run_pack(
        cfg,
        CROSS_MODAL_GRAPH,
        run_id="e2e-cross-modal-3",
        outcomes_by_skill={"content-publish": StageOutcome(status=AWAITING_APPROVAL)},
        ran=ran2,
        manifest=approved,
    )
    assert "format-plan" not in ran2
    assert set(ran2) >= {"text-studio", "image-studio", "video-script", "publish"}
    by_name = {s["name"]: s["status"] for s in m2["stages"]}
    assert by_name["publish"] == AWAITING_APPROVAL
