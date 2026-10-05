"""R1.3: a graph's declared `egress_scope` is built and enforced by the operator-side runner.

`make_executor_from_pack` builds the scope's gate once, before any model call, from the run's
inputs; `execute_stage` hands that same gate to `build_agent_options` for EVERY node, with the
§R2 guard on. A missing, garbled, foreign or unnamed manifest refuses the run up front. Generic:
nothing here knows a pack's name, only the declared scope.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("claude_agent_sdk", reason="SDK not installed")

from agent import egress_scope  # noqa: E402
from agent import pipeline_executor as pe  # noqa: E402
from agent.packs import make_executor_from_pack  # noqa: E402
from agent.pipeline import OK  # noqa: E402
from gtm_core import capture_manifest as cm  # noqa: E402
from gtm_core.packs.loader import PackGraph, PackNode  # noqa: E402

PROFILE = "acme"
URLS = ["https://registry.example.test/members", "https://lists.example.test/banks"]
RUN = "cap-1"


@pytest.fixture(autouse=True)
def _switch_open(monkeypatch):
    monkeypatch.setenv("GTM_SIGNAL_SOURCES_ENABLED", "1")


def _scoped_graph(*node_ids: str) -> PackGraph:
    nodes = tuple(PackNode(id=i, prompt=f"step {i}") for i in (node_ids or ("capture",)))
    return PackGraph(
        pack="p", variant="v", nodes=nodes, internal=True, egress_scope="capture_manifest"
    )


def _plain_graph() -> PackGraph:
    return PackGraph(pack="p", variant="v", nodes=(PackNode(id="a", prompt="step a"),))


def _write_manifest(cfg, run_id=RUN, profile=PROFILE, **over) -> Path:
    args = {"urls": URLS, "cap": 2, "max_age_ms": 86_400_000, **over}
    return cm.write_manifest(profile=profile, run_id=run_id, content_root=cfg.content_root, **args)


# --- the builder ------------------------------------------------------------------------


def test_the_builder_returns_a_gate_over_this_runs_manifest(cfg):
    _write_manifest(cfg)
    gate = egress_scope.build_scope_gate("capture_manifest", cfg, PROFILE, {"manifest_run_id": RUN})
    call = {"url": URLS[0], "formats": ["markdown"], "onlyMainContent": False, "maxAge": 86_400_000}
    assert gate.check("mcp__firecrawl__firecrawl_scrape", call) is None
    assert (
        gate.check("mcp__firecrawl__firecrawl_scrape", {**call, "url": "https://x.example.test/"})
        == "url-not-in-manifest"
    )


def test_the_built_gate_may_read_this_runs_manifest_and_no_other_file(cfg):
    path = _write_manifest(cfg)
    gate = egress_scope.build_scope_gate("capture_manifest", cfg, PROFILE, {"manifest_run_id": RUN})
    assert gate.check("Read", {"file_path": str(path)}) is None
    assert gate.check("Read", {"file_path": str(path.parent / "index.jsonl")}) == "tool-not-allowed"
    assert gate.check("WebFetch", {"url": "https://x.example.test/"}) == "tool-not-allowed"
    assert gate.check("Bash", {"command": "ls"}) == "tool-not-allowed"


@pytest.mark.parametrize(
    "inputs",
    [
        {},
        {"manifest_run_id": ""},
        {"manifest_run_id": "../cap-1"},
        {"manifest_run_id": "a/b"},
        {"manifest_run_id": ".."},
        {"manifest_run_id": "cap-1\n"},
        {"manifest_run_id": 7},
        {"other": RUN},
    ],
)
def test_a_missing_or_unsafe_manifest_input_refuses(cfg, inputs):
    _write_manifest(cfg)
    with pytest.raises(egress_scope.EgressScopeError):
        egress_scope.build_scope_gate("capture_manifest", cfg, PROFILE, inputs)


def test_no_inputs_at_all_refuses(cfg):
    with pytest.raises(egress_scope.EgressScopeError):
        egress_scope.build_scope_gate("capture_manifest", cfg, PROFILE, None)


def test_an_absent_manifest_file_refuses(cfg):
    with pytest.raises(egress_scope.EgressScopeError, match="manifest"):
        egress_scope.build_scope_gate("capture_manifest", cfg, PROFILE, {"manifest_run_id": "nope"})


def test_a_garbled_manifest_refuses(cfg):
    path = _write_manifest(cfg)
    path.write_text("{not json")
    with pytest.raises(egress_scope.EgressScopeError):
        egress_scope.build_scope_gate("capture_manifest", cfg, PROFILE, {"manifest_run_id": RUN})


def test_a_manifest_with_edited_pinned_options_refuses(cfg):
    path = _write_manifest(cfg)
    data = json.loads(path.read_text())
    data["options"]["onlyMainContent"] = True
    path.write_text(json.dumps(data))
    with pytest.raises(egress_scope.EgressScopeError):
        egress_scope.build_scope_gate("capture_manifest", cfg, PROFILE, {"manifest_run_id": RUN})


def test_a_manifest_written_for_another_profile_refuses(cfg):
    foreign = _write_manifest(cfg, profile="other")
    mine = cm.manifest_path_for(PROFILE, RUN, cfg.content_root)
    mine.parent.mkdir(parents=True, exist_ok=True)
    mine.write_text(foreign.read_text())  # a foreign manifest copied into this profile's folder
    with pytest.raises(egress_scope.EgressScopeError, match="profile"):
        egress_scope.build_scope_gate("capture_manifest", cfg, PROFILE, {"manifest_run_id": RUN})


def test_a_manifest_whose_own_run_id_differs_from_its_file_refuses(cfg):
    other = _write_manifest(cfg, run_id="cap-2")
    mine = cm.manifest_path_for(PROFILE, RUN, cfg.content_root)
    mine.write_text(other.read_text())
    with pytest.raises(egress_scope.EgressScopeError, match="run id"):
        egress_scope.build_scope_gate("capture_manifest", cfg, PROFILE, {"manifest_run_id": RUN})


def test_a_closed_source_switch_refuses_even_with_a_good_manifest(cfg, monkeypatch):
    _write_manifest(cfg)
    monkeypatch.delenv("GTM_SIGNAL_SOURCES_ENABLED")
    with pytest.raises(egress_scope.EgressScopeError, match="GTM_SIGNAL_SOURCES_ENABLED"):
        egress_scope.build_scope_gate("capture_manifest", cfg, PROFILE, {"manifest_run_id": RUN})


def test_an_unknown_scope_refuses(cfg):
    with pytest.raises(egress_scope.EgressScopeError):
        egress_scope.build_scope_gate("everything", cfg, PROFILE, {"manifest_run_id": RUN})


# --- the §R2 guard is ON -----------------------------------------------------------------


def test_the_gate_carries_the_cost_cap_guard(cfg, monkeypatch):
    _write_manifest(cfg)
    gate = egress_scope.build_scope_gate("capture_manifest", cfg, PROFILE, {"manifest_run_id": RUN})
    call = {"url": URLS[0], "formats": ["markdown"], "onlyMainContent": False, "maxAge": 86_400_000}
    seen: list[tuple] = []

    def _over_cap(cfg_arg, profile):
        seen.append((cfg_arg, profile))
        return False

    monkeypatch.setattr("agent.budget.vps_budget_ok", _over_cap)
    assert gate.check("mcp__firecrawl__firecrawl_scrape", call) == "budget-exhausted"
    assert gate.used == 0 and seen == [(cfg, PROFILE)]
    monkeypatch.setattr("agent.budget.vps_budget_ok", lambda *_a: True)
    assert gate.check("mcp__firecrawl__firecrawl_scrape", call) is None


# --- the runner -------------------------------------------------------------------------


def _record_options(monkeypatch) -> list[dict]:
    """Stand in for the SDK turn, recording the keyword arguments each node's options get."""
    built: list[dict] = []

    def _fake_options(cfg, profile, **kw):
        built.append(kw)
        return object()

    async def _fake_stream(options, prompt):
        return
        yield  # pragma: no cover - an empty async generator

    monkeypatch.setattr(pe, "build_agent_options", _fake_options)
    monkeypatch.setattr(pe, "stream_brain_messages", _fake_stream)
    return built


def test_the_executor_passes_one_gate_to_build_agent_options_for_every_node(cfg, monkeypatch):
    _write_manifest(cfg)
    built = _record_options(monkeypatch)
    executor = make_executor_from_pack(
        cfg, PROFILE, _scoped_graph("one", "two", "three"), run_inputs={"manifest_run_id": RUN}
    )
    for node in ("one", "two", "three"):
        assert asyncio.run(executor(node, {"run_id": "r1"})).status == OK
    assert len(built) == 3
    gates = [kw["capture_gate"] for kw in built]
    assert all(isinstance(g, cm.CaptureGate) for g in gates)
    assert gates[0] is gates[1] is gates[2]  # one cap across the whole run
    # The gate lives in build_agent_options' default callback: a supplied one is refused there.
    assert all("can_use_tool" not in kw for kw in built)


def test_an_unscoped_graph_is_built_exactly_as_before(cfg, monkeypatch):
    built = _record_options(monkeypatch)
    executor = make_executor_from_pack(cfg, PROFILE, _plain_graph())
    assert asyncio.run(executor("a", {"run_id": "r1"})).status == OK
    assert "capture_gate" not in built[0] and callable(built[0]["can_use_tool"])


@pytest.mark.parametrize(
    "inputs",
    [None, {}, {"manifest_run_id": "absent"}, {"manifest_run_id": "../x"}],
)
def test_a_scoped_graph_with_no_usable_manifest_fails_before_any_model_call(
    cfg, monkeypatch, inputs
):
    built = _record_options(monkeypatch)
    with pytest.raises(egress_scope.EgressScopeError):
        make_executor_from_pack(cfg, PROFILE, _scoped_graph(), run_inputs=inputs)
    assert built == []


def test_a_foreign_manifest_fails_before_any_model_call(cfg, monkeypatch):
    foreign = _write_manifest(cfg, profile="other")
    mine = cm.manifest_path_for(PROFILE, RUN, cfg.content_root)
    mine.parent.mkdir(parents=True, exist_ok=True)
    mine.write_text(foreign.read_text())
    built = _record_options(monkeypatch)
    with pytest.raises(egress_scope.EgressScopeError):
        make_executor_from_pack(cfg, PROFILE, _scoped_graph(), run_inputs={"manifest_run_id": RUN})
    assert built == []


def test_the_generic_pack_cli_cannot_run_a_scoped_graph_ungated(cfg_isolated, monkeypatch):
    """`python -m agent --pack` has no way to pass the manifest input, so it must refuse, not run."""
    from agent.__main__ import _run_pack

    profile_dir = cfg_isolated.profiles_root / PROFILE
    profile_dir.mkdir(parents=True)
    (profile_dir / "packs.toml").write_text('active = ["prospecting"]\n')
    built = _record_options(monkeypatch)
    with pytest.raises(egress_scope.EgressScopeError, match="source_capture"):
        asyncio.run(_run_pack(cfg_isolated, PROFILE, "prospecting", "source-capture"))
    assert built == []
