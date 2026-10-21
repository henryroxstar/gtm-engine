"""H1: every pack graph and inputs file on disk must load and validate.

Auto-covering by construction — parametrized over a glob, so a newly-added pack or
variant is exercised with zero test-file edits. The gap this closes: hand-written,
per-pack test files (test_pack_generality.py, test_pack_loader.py) only ever assert
on the packs someone remembered to write a test for — ``packs/outcomes-loop/graphs/
content-outcomes-loop.toml`` shipped with ZERO coverage until this file existed.

Deliberately thin: this only proves LOADS + fail-closed VALIDATION + engine-conversion
round-trip (depends_on/gate/external_effect preserved). Shape assertions specific to
one pack's design (fan-out, gate placement, spend ordering) stay in their own
hand-written tests — this file's job is the floor every pack must clear, not the
ceiling any one of them reaches for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.packs import pack_graph_to_engine_graph
from gtm_core.packs.loader import load_pack_graph, load_pack_inputs

REPO = Path(__file__).resolve().parents[2]

PACK_GRAPHS = sorted((REPO / "packs").glob("*/graphs/*.toml"))
PACK_INPUTS = sorted((REPO / "packs").glob("*/inputs.toml"))


def _graph_id(path: Path) -> str:
    return f"{path.parent.parent.name}/{path.stem}"


def test_pack_graph_glob_is_non_vacuous():
    """A derived list fails OPEN if the glob ever stops matching — this floor makes
    that failure loud instead of the parametrize below silently collecting nothing."""
    assert len(PACK_GRAPHS) >= 13


def test_pack_inputs_glob_is_non_vacuous():
    assert len(PACK_INPUTS) >= 3


@pytest.mark.parametrize("path", PACK_GRAPHS, ids=_graph_id)
def test_pack_graph_loads_and_validates(path):
    """load_pack_graph runs every fail-closed rule (unknown_skill, unknown_model_role,
    unsafe_gate, unsafe_external_effect, cycle, dangling dep, unbounded revision) —
    a graph that reaches this point without raising cleared all of them."""
    graph = load_pack_graph(path)
    assert graph.nodes, f"{path} loaded with zero nodes"
    node_ids = {n.id for n in graph.nodes}
    assert len(node_ids) == len(graph.nodes), f"{path} has duplicate node ids"


@pytest.mark.parametrize("path", PACK_GRAPHS, ids=_graph_id)
def test_pack_graph_survives_engine_conversion_unchanged(path):
    """The A10 regression this pins: depends_on/gate/external_effect must round-trip
    through pack_graph_to_engine_graph exactly — a silent drop here is how the engine
    used to only recognise Gate 2 by a node's NAME instead of its declaration."""
    pack = load_pack_graph(path)
    engine = pack_graph_to_engine_graph(pack)
    engine_by_id = {n.id: n for n in engine.nodes}
    assert set(engine_by_id) == {n.id for n in pack.nodes}
    for n in pack.nodes:
        e = engine_by_id[n.id]
        assert set(e.depends_on) == set(n.depends_on), f"{path}:{n.id} depends_on drifted"
        assert e.gate == n.gate, f"{path}:{n.id} gate drifted"
        assert e.external_effect == n.external_effect, f"{path}:{n.id} external_effect drifted"


@pytest.mark.parametrize("path", PACK_INPUTS, ids=lambda p: p.parent.name)
def test_pack_inputs_loads_and_validates(path):
    inputs = load_pack_inputs(path)
    # Every declared setting/topic key must be a non-empty string — a loader that
    # accepted a blank key would produce an unresolvable input downstream.
    for s in inputs.settings:
        assert s.key and s.source in ("ask", "derive")
    for k in inputs.knowledge:
        assert k.topic and k.freshness in ("evergreen", "90d")
