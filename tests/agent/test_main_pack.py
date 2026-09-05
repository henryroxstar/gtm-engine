"""CLI pack mode: `--pack`/`--variant` run a pack graph, `--from-node` re-enters one.

The laptop/VPS counterpart to `tests/contracts/test_pack_e2e.py` (which drives
`PipelineRunner` directly) and to `tests/agent/test_main_pipeline.py` (which drives
`_run_pipeline`). Mirrors that file's idioms: a recording executor stand-in, a counting
lock on both module paths, and assertions on the recorded stage list.
"""

from __future__ import annotations

import contextlib
import json

import pytest

pytest.importorskip("agent.pipeline", reason="agent.pipeline not built yet")

from agent.__main__ import _run_pack  # noqa: E402
from agent.graph import Graph, Node  # noqa: E402
from agent.pipeline import OK, PENDING, StageOutcome, new_manifest, reset_from_node  # noqa: E402

PROFILE = "example"
PACK = "prospecting"
VARIANT = "prospect-outreach"
NODES = ("prospect", "dossier", "outreach", "quality", "sequence")


# ── reset_from_node: the primitive --from-node is built on ───────────────────


def _diamond() -> Graph:
    """a → {b, c} → d, plus an independent root e."""
    return Graph(
        nodes=(
            Node(id="a"),
            Node(id="b", depends_on=("a",)),
            Node(id="c", depends_on=("a",)),
            Node(id="d", depends_on=("b", "c")),
            Node(id="e"),
        )
    )


def _all_complete(graph: Graph) -> dict:
    manifest = new_manifest("r1", "cli", PROFILE, graph)
    for stage in manifest["stages"]:
        stage["status"] = OK
        stage["started"] = "T0"
        stage["ended"] = "T1"
        stage["outputs"] = ["stale"]
    return manifest


def test_reset_takes_the_transitive_closure_not_just_the_named_node():
    """Resetting only the named node would run it and then stall.

    `runnable_frontier` decides runnability from a node's OWN status plus its DIRECT
    dependencies — it never re-derives staleness transitively. So a descendant left at
    `ok` is skipped forever, and the run would re-do one node while everything that
    consumed its output kept the previous answer.
    """
    graph, manifest = _diamond(), _all_complete(_diamond())
    assert reset_from_node(graph, manifest, "b") == ["b", "d"]
    statuses = {s["name"]: s["status"] for s in manifest["stages"]}
    assert statuses == {"a": OK, "b": PENDING, "c": OK, "d": PENDING, "e": OK}


def test_reset_leaves_independent_roots_alone():
    """`e` shares no edge with `a`, so a reset from the root must not touch it."""
    graph, manifest = _diamond(), _all_complete(_diamond())
    assert reset_from_node(graph, manifest, "a") == ["a", "b", "c", "d"]
    assert {s["name"]: s["status"] for s in manifest["stages"]}["e"] == OK


def test_reset_clears_per_run_detail():
    """A stale timestamp on a node about to re-run reads as provenance for the new attempt."""
    graph, manifest = _diamond(), _all_complete(_diamond())
    reset_from_node(graph, manifest, "b")
    entry = next(s for s in manifest["stages"] if s["name"] == "b")
    assert not {"started", "ended", "error", "outputs"} & set(entry)


def test_reset_rejects_an_unknown_node():
    """`PipelineRunner._stage_entry` silently APPENDS an unknown stage name, so without
    this check a typo would create a phantom node and quietly reset nothing."""
    with pytest.raises(KeyError):
        reset_from_node(_diamond(), _all_complete(_diamond()), "nope")


# ── the CLI path ─────────────────────────────────────────────────────────────


@pytest.fixture
def pack_harness(monkeypatch):
    """Record dispatched nodes; never touch a real lock, SDK, or skill."""
    ran: list[str] = []

    def _make_executor_from_pack(cfg, profile, pack_graph, **kwargs):
        async def _executor(stage: str, manifest: dict) -> StageOutcome:
            ran.append(stage)
            return StageOutcome(status=OK, outputs=(stage,))

        return _executor

    monkeypatch.setattr("agent.packs.make_executor_from_pack", _make_executor_from_pack)

    @contextlib.contextmanager
    def _noop_lock(content_root, profile, *, blocking=True):
        yield content_root

    monkeypatch.setattr("agent.pipeline.profile_lock", _noop_lock)
    monkeypatch.setattr("agent.__main__.profile_lock", _noop_lock)
    return ran


@pytest.fixture
def activated(cfg_isolated, monkeypatch):
    """Activate the prospecting pack for the test profile, in an ISOLATED profiles root.

    `cfg_isolated`, NOT `cfg`: the plain `cfg` fixture leaves `profiles_root` pointing at the
    real checkout, so writing `packs.toml` through it creates a stray `profiles/example/` in
    the working tree (it did, once). The conftest calls this out — any test that WRITES to
    the profiles tree must use the isolated fixture.
    """
    cfg = cfg_isolated
    packs_toml = cfg.profiles_root / PROFILE / "packs.toml"
    packs_toml.parent.mkdir(parents=True, exist_ok=True)
    packs_toml.write_text(f'active = ["{PACK}"]\n')
    return cfg


def test_pack_run_walks_the_whole_graph(activated, pack_harness):
    """A plain `--pack/--variant` run dispatches every node in dependency order."""
    import asyncio

    rc = asyncio.run(_run_pack(activated, PROFILE, PACK, VARIANT, run_id="r-full"))
    assert rc == 0
    assert pack_harness == list(NODES)


def test_from_node_reruns_only_that_node_and_its_descendants(activated, pack_harness):
    """The behaviour the operator actually asked for: regenerate outreach onward.

    `prospect` and `dossier` are expensive (discovery, research, credits). Re-entering at
    `outreach` must keep them and redo only copy → judge → stage.
    """
    import asyncio

    assert asyncio.run(_run_pack(activated, PROFILE, PACK, VARIANT, run_id="r-two")) == 0
    assert pack_harness == list(NODES)
    pack_harness.clear()

    rc = asyncio.run(
        _run_pack(activated, PROFILE, PACK, VARIANT, from_node="outreach", run_id="r-two")
    )
    assert rc == 0
    assert pack_harness == ["outreach", "quality", "sequence"]
    assert "prospect" not in pack_harness
    assert "dossier" not in pack_harness


def test_from_node_persists_the_reset_to_the_manifest(activated, pack_harness):
    """The re-run must be durable, not only in-memory — a crash mid-re-run resumes correctly."""
    import asyncio

    asyncio.run(_run_pack(activated, PROFILE, PACK, VARIANT, run_id="r-three"))
    asyncio.run(_run_pack(activated, PROFILE, PACK, VARIANT, from_node="quality", run_id="r-three"))
    manifest = json.loads((activated.content_root / PROFILE / "runs" / "r-three.json").read_text())
    assert all(s["status"] == OK for s in manifest["stages"])


def test_inactive_pack_is_refused(cfg_isolated, pack_harness):
    """Fail-closed: the CLI must not be a way around the activation the backend enforces."""
    import asyncio

    (cfg_isolated.profiles_root / PROFILE).mkdir(parents=True, exist_ok=True)
    (cfg_isolated.profiles_root / PROFILE / "packs.toml").write_text('active = ["marketing"]\n')
    assert asyncio.run(_run_pack(cfg_isolated, PROFILE, PACK, VARIANT, run_id="r-x")) == 1
    assert pack_harness == []


def test_missing_packs_toml_is_refused(cfg_isolated, pack_harness):
    """A profile that never opted in reaches no pack — the fail-closed default."""
    import asyncio

    packs_toml = cfg_isolated.profiles_root / PROFILE / "packs.toml"
    assert not packs_toml.exists()  # the isolated profiles root starts empty
    assert asyncio.run(_run_pack(cfg_isolated, PROFILE, PACK, VARIANT, run_id="r-y")) == 1
    assert pack_harness == []


def test_from_node_with_unknown_node_is_refused(activated, pack_harness):
    import asyncio

    asyncio.run(_run_pack(activated, PROFILE, PACK, VARIANT, run_id="r-four"))
    pack_harness.clear()
    rc = asyncio.run(
        _run_pack(activated, PROFILE, PACK, VARIANT, from_node="nope", run_id="r-four")
    )
    assert rc == 1
    assert pack_harness == []
