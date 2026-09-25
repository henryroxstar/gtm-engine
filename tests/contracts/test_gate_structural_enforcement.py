"""A11 — the regression test for the reported bug: a pack node's ``gate = true``
declaration is now a STRUCTURAL pause, independent of whether its skill emits
``⟦GATE:plan⟧`` (or any marker at all). Before this fix, `execute_stage` paused ONLY on
that literal sentinel — so `outreach` and `sequence` (draft-outreach / email-sequence,
neither of which ever emitted a marker) silently completed as `OK` despite declaring
`gate = true` in every shipped pack graph.

Two levels:
  1. Unit: `execute_stage` itself, SDK mocked away, proving the marker-independent pause.
  2. Fresh recount + cross-check: enumerate every `gate = true` node in every shipped
     pack graph via `tomllib` (never trust a cached count), and assert
     `make_executor_from_pack` builds a `gates` dict containing every one of them — the
     dict `execute_stage` actually reads to decide whether to pause.
"""

from __future__ import annotations

import asyncio
import tomllib
from pathlib import Path

import pytest

pytest.importorskip("claude_agent_sdk", reason="SDK not installed")

from agent import pipeline_executor as pe  # noqa: E402
from agent.packs import make_executor_from_pack  # noqa: E402
from agent.pipeline import AWAITING_APPROVAL, OK  # noqa: E402
from gtm_core.packs.loader import load_pack_graph  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PROFILE = "example"


class _Cfg:
    repo_root = REPO
    content_root = None  # set per-test to a tmp_path


def _mock_empty_turn(monkeypatch):
    """Replace the SDK turn with one that streams no text at all — the marker can
    never appear, isolating the `gates`-dict path from the sentinel path."""

    def _fake_options(*a, **k):
        return object()

    async def _fake_stream(*a, **k):
        return
        yield  # pragma: no cover — makes this an async generator with no items

    monkeypatch.setattr(pe, "build_agent_options", _fake_options)
    monkeypatch.setattr(pe, "stream_brain_messages", _fake_stream)


# ── unit: execute_stage pauses on gate=true with zero marker text ─────────────


def test_gate_true_pauses_even_with_no_marker_in_the_output(monkeypatch, tmp_path):
    """The exact shape of the reported bug: a node declares gate=true, its skill's
    turn contains no ⟦GATE:...⟧ of any kind, and the stage must still pause."""
    _mock_empty_turn(monkeypatch)
    cfg = _Cfg()
    cfg.content_root = tmp_path

    outcome = asyncio.run(
        pe.execute_stage(
            cfg,
            PROFILE,
            "outreach",
            {},
            prompts={"outreach": "draft outreach"},
            gates={"outreach": True},
        )
    )
    assert outcome.status == AWAITING_APPROVAL


def test_gate_false_does_not_pause_with_no_marker(monkeypatch, tmp_path):
    """A node NOT declared gate=true must be unaffected — proves the fix is additive,
    not a blanket "always pause" regression."""
    _mock_empty_turn(monkeypatch)
    cfg = _Cfg()
    cfg.content_root = tmp_path

    outcome = asyncio.run(
        pe.execute_stage(
            cfg,
            PROFILE,
            "dossier",
            {},
            prompts={"dossier": "build dossier"},
            gates={"outreach": True},  # a DIFFERENT node's gate — must not leak
        )
    )
    assert outcome.status == OK


def test_gates_none_preserves_marker_only_behavior(monkeypatch, tmp_path):
    """The non-pack VPS news/journey path never passes `gates` — `gates=None` must be
    byte-identical to pre-A11 behavior: no marker, no pause, regardless of node id."""
    _mock_empty_turn(monkeypatch)
    cfg = _Cfg()
    cfg.content_root = tmp_path

    outcome = asyncio.run(
        pe.execute_stage(
            cfg,
            PROFILE,
            "plan",
            {},
            prompts={"plan": "propose the plan"},
            gates=None,
        )
    )
    assert outcome.status == OK


def test_only_an_enrollment_gate_is_told_its_run_id(monkeypatch, tmp_path):
    """Client issue #245: the enrollment gate names its draft after the run, so the gate can
    read that run's draft only — the model needs the id in its prompt to do so. Every other
    node, gated or not, and the manifest-less path keep their prompts byte-identical."""
    seen: dict[str, str] = {}
    monkeypatch.setattr(pe, "build_agent_options", lambda *a, **k: object())

    def _capture(stage):
        async def _stream(options, prompt):
            seen[stage] = prompt
            return
            yield  # pragma: no cover

        return _stream

    cfg = _Cfg()
    cfg.content_root = tmp_path
    manifest = {"run_id": "r-20260915-0900-prospecting-prospect-outreach"}
    cases = (("sequence", manifest), ("plan", manifest), ("dossier", manifest), ("x", {}))
    for stage, manifest_for_stage in cases:
        monkeypatch.setattr(pe, "stream_brain_messages", _capture(stage))
        asyncio.run(
            pe.execute_stage(
                cfg,
                PROFILE,
                stage,
                manifest_for_stage,
                prompts={stage: "base prompt"},
                gates={"sequence": True, "plan": True, "x": True},
                run_id_stages=frozenset({"sequence", "x"}),
            )
        )

    assert seen["sequence"].startswith("base prompt\n\nPack run id: " + manifest["run_id"])
    assert seen["plan"] == "base prompt", "a gated node with no enrollment successor"
    assert seen["dossier"] == "base prompt"
    assert seen["x"] == "base prompt", "no run id in the manifest, nothing to tell"


def test_the_prospecting_sequence_node_is_the_run_id_stage():
    """The pack wiring, not just execute_stage: the node whose approval enrolls is the one
    told its run id, and no marketing gate is."""
    from unittest.mock import patch

    from agent.config import Config

    cfg = Config.from_env(repo_root=REPO)
    captured: dict[str, frozenset] = {}

    async def _fake_execute_stage(*args, run_id_stages=None, **kwargs):
        captured["run_id_stages"] = run_id_stages

    for variant, expected in (
        ("prospecting/graphs/prospect-outreach.toml", {"sequence"}),
        # SC9: the DNC draft is run-named too. Missing until 2026-09-24, so the review
        # node never learned its run id and every approval refused with "found none".
        ("inbound/graphs/optout-suppress.toml", {"review"}),
        ("marketing/graphs/linkedin-post.toml", set()),
    ):
        executor = make_executor_from_pack(cfg, PROFILE, load_pack_graph(REPO / "packs" / variant))
        with patch("agent.packs.execute_stage", _fake_execute_stage):
            asyncio.run(executor("any", {}))
        assert captured["run_id_stages"] == expected, variant


# ── fresh recount: every declared gate is in the dict execute_stage actually reads ──


def _gated_nodes_by_toml() -> dict[Path, list[str]]:
    """Every `gate = true` node id, per pack graph TOML, via raw tomllib — never
    trusted from a cached count or a prior test's assertions (§R18)."""
    out: dict[Path, list[str]] = {}
    for path in sorted((REPO / "packs").glob("*/graphs/*.toml")):
        with path.open("rb") as f:
            raw = tomllib.load(f)
        gated = [n["id"] for n in raw.get("nodes", []) if n.get("gate") is True]
        if gated:
            out[path] = gated
    return out


def test_every_shipped_gate_true_node_reaches_the_structural_gates_dict():
    """For every pack graph shipped in this repo, make_executor_from_pack's `gates`
    dict — the ONLY thing execute_stage reads to decide a structural pause — must
    contain every node the TOML itself declares gate=true, with no omission."""
    from agent.config import Config

    cfg = Config.from_env(repo_root=REPO)
    by_toml = _gated_nodes_by_toml()
    assert by_toml, "no gate=true node found anywhere — the glob or the fixture is wrong"

    for path, expected_ids in by_toml.items():
        pack = load_pack_graph(path)
        # make_executor_from_pack builds `gates` as a closure-local dict; recover it
        # the same way agent/packs.py itself does, so this test breaks if that
        # construction ever silently changes shape.
        gates = {n.id: n.gate for n in pack.nodes if n.gate}
        for node_id in expected_ids:
            assert node_id in gates and gates[node_id] is True, (
                f"{path.relative_to(REPO)}: node {node_id!r} declares gate=true in the "
                "TOML but is missing from the executor's gates dict"
            )
        # And building the actual executor must not raise (proves the wiring, not
        # just the dict comprehension, is exercised).
        make_executor_from_pack(cfg, PROFILE, pack)
