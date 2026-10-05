"""`python -m agent.source_capture`: the operator's one command for the capture step.

It opens only with the source-list switch, writes the manifest of what is due, then runs the
internal `source-capture` graph with that manifest named as its run input. The model turn is stubbed
(`agent.packs.execute_stage`); everything else, the runner, the manifest and the gate, is real.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
from pathlib import Path

import pytest

from agent import source_capture
from agent.config import Config
from agent.pipeline import FAILED, OK, StageOutcome
from gtm_core.capture_manifest import CaptureGate
from gtm_core.signal_sources import sources_dir_for, store_capture
from unit.conftest import SOURCE_URL

REPO = Path(__file__).resolve().parents[2]
PINNED = {"formats": ["markdown"], "onlyMainContent": False, "maxAge": 86_400_000}
SCRAPE = "mcp__firecrawl__firecrawl_scrape"


@pytest.fixture
def cfg(signal_world) -> Config:
    (signal_world.profiles_root / signal_world.profile / "packs.toml").write_text(
        'active = ["prospecting"]\n', encoding="utf-8"
    )
    return dataclasses.replace(
        Config.from_env(repo_root=REPO),
        content_root=signal_world.content_root,
        profiles_root=signal_world.profiles_root,
    )


class _Calls(list):
    """The recorded stage calls, plus the outcome the stubbed turn will return."""

    result: dict


@pytest.fixture
def stage(monkeypatch):
    """Replace the model turn; record every call's keyword arguments."""
    calls = _Calls()
    result = {"outcome": StageOutcome(status=OK, outputs=("capture",)), "files_pages": True}

    async def _fake(cfg, profile, stage_name, manifest, **kw):
        calls.append({"stage": stage_name, "run_id": manifest["run_id"], **kw})
        if result["files_pages"]:
            # What the capture hook does for every page the brain scrapes.
            for url in kw["capture_gate"]._manifest.urls:
                store_capture(
                    url, "# page\n", sources_dir=sources_dir_for(profile, cfg.content_root)
                )
        return result["outcome"]

    monkeypatch.setattr("agent.packs.execute_stage", _fake)
    calls.result = result
    return calls


def _run(cfg, world, **kw):
    return source_capture.run(cfg, world.profile, world.product, **kw)


def _manifests(world):
    return sorted(world.sources_dir.glob("manifest-*.json")) if world.sources_dir.exists() else []


def test_a_due_source_is_written_to_a_manifest_and_captured_under_its_gate(
    cfg, signal_world, stage, capsys
):
    assert _run(cfg, signal_world, run_id="cap-1") == 0

    (manifest,) = _manifests(signal_world)
    assert manifest.name == "manifest-cap-1.json"
    assert json.loads(manifest.read_text())["urls"] == [SOURCE_URL]
    (call,) = stage
    assert call["stage"] == "capture"
    gate = call["capture_gate"]
    assert isinstance(gate, CaptureGate)
    assert gate.check(SCRAPE, {"url": SOURCE_URL, **PINNED}) is None
    assert gate.check(SCRAPE, {"url": "https://elsewhere.example.test/", **PINNED}) is not None
    out = capsys.readouterr().out
    assert "manifest-cap-1.json" in out and "1 page" in out and "capture" in out


def test_a_closed_switch_does_nothing_and_says_so(cfg, signal_world, stage, monkeypatch, capsys):
    monkeypatch.delenv("GTM_SIGNAL_SOURCES_ENABLED")
    assert _run(cfg, signal_world, run_id="cap-1") == 0
    assert "GTM_SIGNAL_SOURCES_ENABLED" in capsys.readouterr().out
    assert _manifests(signal_world) == [] and stage == []


def test_nothing_due_runs_no_model_turn(cfg, signal_world, stage, capsys):
    from unit.conftest import page

    today = datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat()
    signal_world.capture(page("Northwind Traders"), today)
    assert _run(cfg, signal_world, run_id="cap-1") == 0
    assert "Nothing is due" in capsys.readouterr().out
    assert _manifests(signal_world) == [] and stage == []


def test_a_profile_that_has_not_activated_the_pack_is_refused_before_anything_is_written(
    cfg, signal_world, stage, capsys
):
    (signal_world.profiles_root / signal_world.profile / "packs.toml").write_text(
        'active = ["marketing"]\n', encoding="utf-8"
    )
    assert _run(cfg, signal_world, run_id="cap-1") == 2
    assert "packs.toml" in capsys.readouterr().out
    assert _manifests(signal_world) == [] and stage == []


@pytest.mark.parametrize("bad", ["../x", "a/b", "..", "", "x y", "x\n"])
def test_an_unsafe_run_id_is_refused_before_anything_is_written(cfg, signal_world, stage, bad):
    assert _run(cfg, signal_world, run_id=bad) == 2
    assert _manifests(signal_world) == [] and stage == []


def test_a_failed_capture_turn_exits_non_zero(cfg, signal_world, stage):
    stage.result["outcome"] = StageOutcome(status=FAILED, error="boom")
    assert _run(cfg, signal_world, run_id="cap-1") == 1


def test_an_exhausted_monthly_cap_stops_the_run_before_any_model_turn(
    cfg, signal_world, stage, monkeypatch
):
    monkeypatch.setattr("agent.budget.vps_budget_ok", lambda *_a: False)
    assert _run(cfg, signal_world, run_id="cap-1") == 1
    assert stage == []


def test_the_run_id_defaults_to_a_fresh_one_so_two_runs_never_share_a_manifest(
    cfg, signal_world, stage
):
    assert _run(cfg, signal_world) == 0
    (manifest,) = _manifests(signal_world)
    assert manifest.name.startswith("manifest-cap-") and manifest.name != "manifest-cap-.json"


def test_a_run_that_reports_ok_but_filed_no_page_exits_non_zero_and_names_the_page(
    cfg, signal_world, stage, capsys
):
    stage.result["files_pages"] = False
    assert _run(cfg, signal_world, run_id="cap-1") == 1
    out = capsys.readouterr().out
    assert SOURCE_URL in out and "not filed" in out


def test_a_page_filed_before_the_run_started_does_not_count(cfg, signal_world, stage, capsys):
    stage.result["files_pages"] = False
    signal_world.capture("# old\n", "2020-01-01T00:00:00+00:00")
    assert _run(cfg, signal_world, run_id="cap-1") == 1
    assert SOURCE_URL in capsys.readouterr().out


def test_more_due_than_the_page_budget_defers_the_rest_and_says_how_many(
    cfg, signal_world, stage, capsys, monkeypatch
):
    from types import SimpleNamespace

    from gtm_core.signal_obs import due

    urls = [f"https://r{i}.example.test/" for i in range(7)]
    report = due.DueReport(
        registry=None, due=[(SimpleNamespace(url=u), "never captured") for u in urls]
    )
    monkeypatch.setattr(due, "due_sources", lambda *a, **k: report)
    (signal_world.content_root / signal_world.profile / "settings.json").write_text(
        json.dumps({"observer_id": "amy", "signal_monitor_max_captures": 3}), encoding="utf-8"
    )
    assert _run(cfg, signal_world, run_id="cap-2") == 0
    (manifest,) = _manifests(signal_world)
    data = json.loads(manifest.read_text())
    assert data["urls"] == urls[:3] and data["cap"] == 3
    out = capsys.readouterr().out
    assert "4 more are due" in out and "next run" in out
