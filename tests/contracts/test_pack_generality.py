"""E-4 verification: the generality proof.

- Two more real pack graphs (prospecting, solution-architecture) load on the unmodified
  engine, alongside a marketing fan-out variant (long-form-blog).
- A fan-out batch is genuinely dispatched concurrently: one manifest persist shows BOTH
  branch nodes ``running`` at once, not one-then-the-other.
- Parallel execution still honors exactly one ``profile_lock`` acquisition regardless of
  fan-out width, and the §R2 budget guard now fires per dispatch batch, not just once at
  run start.
- No engine module special-cases a pack/variant name in its control flow (AST-based, so
  a docstring/comment mentioning a pack by way of example — of which there are several —
  doesn't false-positive).
"""

from __future__ import annotations

import ast
import asyncio
import copy
import dataclasses
from pathlib import Path

import pytest

from agent.graph import Graph, Node
from agent.packs import load_engine_graph
from agent.pipeline import FAILED, OK, PipelineRunner, StageOutcome
from gtm_core.packs.loader import load_pack_graph

REPO = Path(__file__).resolve().parents[2]
PROFILE = "example"


@pytest.fixture()
def cfg(tmp_path):
    from agent.config import Config

    base = Config.from_env(repo_root=REPO)
    return dataclasses.replace(base, content_root=tmp_path / "content")


# ── two more real pack graphs load on the unmodified engine ──────────────────


def test_prospecting_pack_loads_and_shapes_correctly():
    """The prospecting pack runs end to end: discovery through a staged, PAUSED sequence.

    ONE human pause (the pack gate rule — pause only where a decision guards spend or egress): `sequence` drafts
    the sequence + the lead-enrollment plan and pauses for the PII-egress confirmation, where
    the operator reviews the finished copy and the lead list together. `outreach` does NOT
    pause — it guarded neither spend nor egress. `sequence-enroll` is the actual enrollment
    call, dispatched by Python only (never the brain) after that gate is approved — the same
    split as the publish gate. The `quality` node (the judge) ranks and never gates; the
    deterministic check is what refuses a row.
    """
    pack = load_pack_graph(REPO / "packs" / "prospecting" / "graphs" / "prospect-outreach.toml")
    assert pack.ids == ("prospect", "dossier", "outreach", "quality", "sequence", "sequence-enroll")
    assert pack.node("outreach").gate is False  # one pause, at sequence
    assert pack.node("sequence").gate is True
    assert pack.node("sequence").external_effect is None
    assert pack.node("sequence-enroll").gate is True
    assert pack.node("sequence-enroll").external_effect == "email_enroll"
    assert pack.node("sequence-enroll").depends_on == ("sequence",)
    assert pack.node("prospect").gate is False
    assert pack.node("quality").gate is False  # the judge ranks; it never gates


def test_prospecting_pack_is_all_brain_plan_and_pii_bearing():
    """Every node carries customer PII, so none may route to a worker model (CLAUDE.md
    model discipline: brain_plan for all gate-critical or PII-handling stages)."""
    pack = load_pack_graph(REPO / "packs" / "prospecting" / "graphs" / "prospect-outreach.toml")
    assert all(n.model_role == "brain_plan" for n in pack.nodes)


def test_every_shipped_pack_variant_still_loads():
    """Regression guard for the `unsupported_revision` rule: it must break no shipped pack.

    Supersedes an earlier per-pack assertion that the prospecting graph declared no
    `revisable_from`/`max_visits`. That is now enforced by the loader itself for EVERY pack
    (`validate_node_semantics`), so asserting it here would only restate the rule. What is
    still worth pinning is the blast radius — that turning the rule on did not make any
    committed variant unloadable.
    """
    variants = sorted((REPO / "packs").glob("*/graphs/*.toml"))
    assert variants, "no pack variants found — glob is wrong, not the packs"
    for path in variants:
        pack = load_pack_graph(path)  # raises PackValidationError if the rule bites
        assert pack.nodes, f"{path.name} loaded with no nodes"


SOLUTION_ARCHITECTURE = (
    REPO / "packs" / "solution-architecture" / "graphs" / "solution-architecture.toml"
)


def test_solution_architecture_pack_loads_and_shapes_correctly():
    """A DAG since 2026-09-22 (SA7): `design` fans out, and the commercial half is on the graph.

    `scope-check` after the design, not before it — post-design is the richer mode, with a
    concrete solution to simplify. `proposal` after `scope-check`, because a price quoted before
    the buyer confirms scope is a price for work nobody agreed to.
    """
    pack = load_pack_graph(SOLUTION_ARCHITECTURE)
    assert pack.ids == ("discovery", "design", "runbook", "deck", "scope-check", "proposal")
    assert all(not n.gate for n in pack.nodes)  # produces docs, no external gate
    by_id = {n.id: n for n in pack.nodes}
    assert by_id["scope-check"].depends_on == ("design",)
    assert by_id["proposal"].depends_on == ("scope-check",)


def test_solution_architecture_design_fans_out_into_the_frontier(cfg):
    """Engine-side, and the reason this is a separate test: that the TOML parses proves the file
    is well-formed, never that the runner treats the two new nodes as a fan-out. Once `design`
    completes, `runbook` and `scope-check` are both runnable in ONE frontier and are dispatched
    concurrently — asserting only on `pack.ids` would pass on a graph the runner walks linearly.
    """
    from agent.pipeline import OK, new_manifest, runnable_frontier

    _, engine_graph = load_engine_graph(SOLUTION_ARCHITECTURE)
    manifest = new_manifest("r-sa", "cron", PROFILE, graph=engine_graph)
    assert set(runnable_frontier(engine_graph, manifest)) == {"discovery"}
    stages = {s["name"]: s for s in manifest["stages"]}
    for stage in ("discovery", "design"):
        stages[stage]["status"] = OK
    assert set(runnable_frontier(engine_graph, manifest)) == {"runbook", "scope-check"}
    stages["scope-check"]["status"] = OK
    assert set(runnable_frontier(engine_graph, manifest)) == {"runbook", "proposal"}


def test_solution_architecture_stays_free_and_stub_free(cfg):
    """SA7's pricing half, verified rather than assumed.

    Adding a node above `free` reprices the WHOLE graph, and an `oss = "private"` node makes the
    pack stub-bearing — which then needs a `[carve].stub_bearing_graphs` declaration and an entry
    in the pinned roster. Both new skills derive `free` + `public`, so neither happens; this is
    what fails the day one of them is repriced.
    """
    from gtm_core import gating

    pack = load_pack_graph(SOLUTION_ARCHITECTURE)
    skills = [n.skill for n in pack.nodes]
    assert gating.derive_graph_floor(skills) == "free"
    assert {s: gating.oss_visibility(s) for s in skills} == dict.fromkeys(skills, "public")


def test_planning_pack_is_three_independent_roots_no_gate():
    """The planning pack is a BATCH, not a chain: three standalone deliverables with no
    depends_on between them and no external gate. This is the multi-root fan-out shape —
    all three nodes are in the initial frontier at once."""
    pack = load_pack_graph(REPO / "packs" / "planning" / "graphs" / "planning.toml")
    assert pack.ids == ("gtm-plan", "account-plan", "event-plan")
    assert all(n.depends_on == () for n in pack.nodes)  # independent — no false chain
    assert all(not n.gate for n in pack.nodes)  # produces docs/spreadsheets, nothing sent


def test_planning_pack_converts_to_a_three_root_engine_graph(cfg):
    """Engine-side: with no edges, every node is immediately runnable — the runner's very
    first frontier is all three, dispatched concurrently under one lock (E-4 fan-out)."""
    from agent.pipeline import DEFAULT_GRAPH, new_manifest, runnable_frontier

    _, engine_graph = load_engine_graph(REPO / "packs" / "planning" / "graphs" / "planning.toml")
    manifest = new_manifest("r-plan", "cron", PROFILE, graph=engine_graph)
    assert set(runnable_frontier(engine_graph, manifest)) == {
        "gtm-plan",
        "account-plan",
        "event-plan",
    }
    assert engine_graph is not DEFAULT_GRAPH  # sanity: it's the pack's own graph


def test_long_form_blog_pack_is_a_real_fan_out():
    _, engine_graph = load_engine_graph(
        REPO / "packs" / "marketing" / "graphs" / "long-form-blog.toml"
    )
    studio = engine_graph.node("studio")
    assert set(studio.depends_on) == {"research-evidence", "research-competitive"}


def test_case_study_pack_is_a_single_gated_pii_node():
    """A pack graph can be a SINGLE node — the degenerate shape, and still a graph. The
    gate is on a node with no external_effect (allowed: the loader's unsafe_gate rule only
    fires outside {None, "publish"}), because the artifact names a real customer and must
    stop for approval before it is treated as an asset. brain_plan is load-bearing, not
    stylistic: this stage carries customer PII."""
    pack = load_pack_graph(REPO / "packs" / "marketing" / "graphs" / "case-study.toml")
    assert pack.ids == ("case-study",)
    node = pack.node("case-study")
    assert node.gate is True
    assert node.external_effect is None  # gated for approval, not for egress
    assert node.model_role == "brain_plan"  # customer-PII-bearing — never brain_radar


def test_content_outcomes_loop_pack_loads_as_read_only_learning_loop():
    """The content outcomes variant loads on the unmodified engine: no gate, no external
    effect, and it routes through content-outcomes-sync so hook_id tagging stays in the
    skill's body_template rather than being hardcoded in pack data."""
    pack = load_pack_graph(
        REPO / "packs" / "outcomes-loop" / "graphs" / "content-outcomes-loop.toml"
    )
    assert pack.ids == ("content_sync",)
    node = pack.node("content_sync")
    assert node.gate is False
    assert node.external_effect is None
    assert node.skill == "content-outcomes-sync"
    assert node.model_role == "brain_plan"


CREATOR_GRAPH = REPO / "packs" / "creator" / "graphs" / "short-form-video.toml"


def test_creator_pack_gates_bracket_the_spend():
    """The creator pack is the first pack whose middle stages cost real money, so the
    gates are not decoration: `plan` stops before anything is scripted, `storyboard`
    stops before any paid video render spend, `publish` stops before anything ships, and
    every node between them is side-effect-free by construction (the loader's
    unsafe_external_effect rule). `publish` is the only external effect in the graph — a
    render node that declared one would fail to load."""
    pack = load_pack_graph(CREATOR_GRAPH)
    assert [n.id for n in pack.nodes if n.gate] == ["plan", "storyboard", "publish"]
    assert [n.id for n in pack.nodes if n.external_effect] == ["publish"]
    assert pack.node("publish").external_effect == "publish"


def test_creator_pack_renders_are_siblings_that_join_at_finish():
    """Volume comes from sibling nodes, not from an engine change: both renders depend only
    on the approved `storyboard`, so the frontier dispatches them in ONE batch, and `finish`
    cannot start until both are complete — it finishes every variant before `score` sees any
    of them (Phase A's pack/graph changes and the storyboard gate)."""
    _, engine_graph = load_engine_graph(CREATOR_GRAPH)
    renders = ("render-vertical", "render-feed")
    for r in renders:
        assert engine_graph.node(r).depends_on == ("storyboard",)
    assert set(engine_graph.node("finish").depends_on) == set(renders)
    assert engine_graph.node("score").depends_on == ("finish",)


def test_creator_pack_pays_for_renders_on_brain_plan():
    """Both render nodes hold the spending decision (cap precheck, cost preflight, the
    stop-over-cap), so they stay on brain_plan. `score` is mechanical ranking over non-PII
    craft signals with a human gate downstream, so brain_radar is fine there."""
    pack = load_pack_graph(CREATOR_GRAPH)
    for node_id in ("render-vertical", "render-feed"):
        assert pack.node(node_id).model_role == "brain_plan"
    assert pack.node("score").model_role == "brain_radar"


def test_creator_pack_render_nodes_share_one_skill_with_different_prompts():
    """The `render_*` fan-out is one skill pointed at by N sibling nodes — the same idiom
    long-form-blog uses for its two research nodes. If these prompts were identical the
    fan-out would be pure duplicated spend, so the differing prompt IS the contract."""
    pack = load_pack_graph(CREATOR_GRAPH)
    vertical, feed = pack.node("render-vertical"), pack.node("render-feed")
    assert vertical.skill == feed.skill == "video-render"
    assert vertical.prompt != feed.prompt


# ── fan-out: a real batch shows BOTH nodes RUNNING at once ────────────────────


def test_fan_out_batch_shows_concurrent_running_nodes_in_one_persist(cfg):
    """Prove the batch is dispatched together, not one-after-another: capture every
    persisted manifest snapshot and find one where BOTH branch nodes are 'running'."""
    graph = Graph(
        nodes=(
            Node("a"),
            Node("b", depends_on=("a",)),
            Node("c", depends_on=("a",)),
            Node("d", depends_on=("b", "c")),
        )
    )
    runner = PipelineRunner(cfg, PROFILE, graph=graph)
    snapshots: list[dict] = []
    real_write = runner.ledgers.write_run_manifest

    def _capturing_write(manifest):
        snapshots.append(copy.deepcopy(manifest))
        return real_write(manifest)

    runner.ledgers.write_run_manifest = _capturing_write

    async def _executor(stage: str, manifest: dict) -> StageOutcome:
        return StageOutcome(status=OK)

    asyncio.run(runner.run("r-concurrent", "cron", _executor))

    both_running = [
        snap
        for snap in snapshots
        if {s["name"]: s["status"] for s in snap["stages"]}.get("b") == "running"
        and {s["name"]: s["status"] for s in snap["stages"]}.get("c") == "running"
    ]
    assert both_running, "no persisted snapshot ever showed b AND c running at the same time"


def test_fan_out_still_holds_exactly_one_profile_lock(cfg, monkeypatch):
    import contextlib

    lock_calls = {"n": 0}

    @contextlib.contextmanager
    def _counting_lock(content_root, profile, *, blocking=True):
        lock_calls["n"] += 1
        yield content_root

    monkeypatch.setattr("agent.pipeline.profile_lock", _counting_lock)

    graph = Graph(nodes=(Node("a"), Node("b", depends_on=("a",)), Node("c", depends_on=("a",))))
    runner = PipelineRunner(cfg, PROFILE, graph=graph)

    async def _executor(stage: str, manifest: dict) -> StageOutcome:
        return StageOutcome(status=OK)

    asyncio.run(runner.run("r-lock-count", "cron", _executor))
    assert lock_calls["n"] == 1  # one acquisition for the whole run, fan-out included


def test_budget_guard_fires_per_batch_not_just_once(cfg, monkeypatch):
    """§R2: a profile that goes over budget mid-run (after the first batch) must still
    abort before the SECOND batch — not just get checked once at the very start."""
    calls = {"n": 0}

    def _budget_ok(cfg_arg, profile):
        calls["n"] += 1
        return calls["n"] == 1  # ok for the first batch, over-cap from the second on

    monkeypatch.setattr("agent.budget.vps_budget_ok", _budget_ok)

    graph = Graph(nodes=(Node("a"), Node("b", depends_on=("a",))))
    runner = PipelineRunner(cfg, PROFILE, graph=graph)
    ran: list[str] = []

    async def _executor(stage: str, manifest: dict) -> StageOutcome:
        ran.append(stage)
        return StageOutcome(status=OK)

    manifest = asyncio.run(runner.run("r-budget", "cron", _executor))
    by_name = {s["name"]: s["status"] for s in manifest["stages"]}
    errors_by_name = {s["name"]: s.get("error", "") for s in manifest["stages"]}

    assert ran == ["a"]  # stopped before b's batch ever dispatched
    assert by_name["a"] == OK
    assert by_name["b"] == FAILED
    assert "cost cap" in errors_by_name["b"]


# ── engine purity: no engine module special-cases a pack name ────────────────

ENGINE_MODULES = (
    "agent/pipeline.py",
    "agent/graph.py",
    "agent/pipeline_executor.py",
    "agent/packs.py",
    "agent/readiness.py",
    "gtm_core/packs/loader.py",
    "gtm_core/packs/tenant.py",
    "gtm_core/packs/reachability.py",
    "gtm_core/knowledge_index.py",
)


def _pack_and_variant_names() -> frozenset[str]:
    """Every pack name and variant name that exists on disk.

    Derived, not hand-listed. The literal set this replaced had gone stale in both
    directions — it named two things that are not packs at all, and had missed every pack
    added since it was written (case-study, knowledge-refresh, outcomes-loop, creator),
    so the purity check silently stopped covering the newest packs, which are exactly the
    ones most likely to have tempted an engine special-case.
    """
    names: set[str] = set()
    for graph in (REPO / "packs").glob("*/graphs/*.toml"):
        names.add(graph.parent.parent.name)  # pack
        names.add(graph.stem)  # variant
    return frozenset(names)


PACK_AND_VARIANT_NAMES = _pack_and_variant_names()


def _string_constants_in_conditionals(tree: ast.AST) -> list[str]:
    """String literals appearing in a Compare (==, in, etc.) anywhere in the module —
    AST-based so a docstring/comment mentioning a pack by way of example never
    false-positives (only executable comparison code is inspected)."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            for operand in operands:
                if isinstance(operand, ast.Constant) and isinstance(operand.value, str):
                    found.append(operand.value)
    return found


def _module_special_cases_a_pack(path: Path, pack_names) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [s for s in _string_constants_in_conditionals(tree) if s in pack_names]


#: One entry per KNOWN pack + KNOWN variant (not a count) — so removing any single one
#: of these fails on a self-explanatory diff naming exactly what disappeared, rather
#: than a bare "len() < N" that gives no clue which pack went missing. Adding a new
#: pack/variant never fails this (it's a subset check) — extend the set when a fix
#: elsewhere in this file needs the new name pinned, not before.
_KNOWN_PACKS_AND_VARIANTS = frozenset(
    {
        "creator",
        "short-form-video",
        "cross-modal-campaign",
        "repurpose-clips",
        "restyle-shorts",
        "knowledge-refresh",
        "marketing",
        "case-study",
        "linkedin-post",
        "long-form-blog",
        "outcomes-loop",
        "content-outcomes-loop",
        "planning",
        "prospecting",
        "prospect-outreach",
        "solution-architecture",
    }
)


def test_pack_name_set_is_derived_and_non_vacuous():
    """A derived set fails OPEN if the glob ever stops matching — the purity check below
    would pass by having nothing to look for. Pin every known pack/variant name."""
    assert _KNOWN_PACKS_AND_VARIANTS <= PACK_AND_VARIANT_NAMES


def test_no_engine_module_special_cases_a_pack_name():
    violations = {}
    for rel in ENGINE_MODULES:
        hits = _module_special_cases_a_pack(REPO / rel, PACK_AND_VARIANT_NAMES)
        if hits:
            violations[rel] = hits
    assert violations == {}, violations


def test_engine_purity_checker_self_test_detects_a_synthetic_violation(tmp_path):
    """Prove the checker actually fires — mirrors the layering-test self-test pattern."""
    bad_module = tmp_path / "bad_engine_module.py"
    bad_module.write_text(
        'def dispatch(pack_name):\n    if pack_name == "marketing":\n        pass\n'
    )
    hits = _module_special_cases_a_pack(bad_module, PACK_AND_VARIANT_NAMES)
    assert hits == ["marketing"]
