"""Tests for gtm_core.run_summary (machine-readable structured run summary)."""

from __future__ import annotations

from pathlib import Path

from gtm_core.run_state import RunState, save_run_state
from gtm_core.run_summary import (
    RunSummary,
    build_run_summary,
    load_run_summary,
    save_run_summary,
)


def test_build_run_summary_from_state_and_ledgers(tmp_path: Path) -> None:
    profile = "test-tenant"
    run_id = "run-20260918-test"

    prospects_dir = tmp_path / profile / "prospects"
    prospects_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create run_state.json
    state = RunState.new(profile=profile, mode="full", run_id=run_id)
    state.start_stage("init")
    state.complete_stage("init", metrics={"connectors": {"vibe": "ok", "rocketreach": "ok"}})
    state.start_stage("discovery")
    state.complete_stage("discovery", metrics={"accounts_discovered": 20, "cost_usd": 1.5})
    state.start_stage("gate_score")
    state.complete_stage("gate_score", metrics={"accounts_gated": 15, "accounts_dropped": 5})
    state.start_stage("enrichment")
    state.complete_stage("enrichment", metrics={"accounts_enriched": 12, "cost_usd": 3.0})
    state.start_stage("output")
    state.complete_stage("output", metrics={"accounts_ready": 10, "accounts_generic": 2})

    state_path = prospects_dir / "run_state.json"
    save_run_state(state, state_path)

    # 2. Create costs.jsonl
    costs_jsonl = tmp_path / profile / "costs.jsonl"
    costs_jsonl.write_text(
        '{"ts": "2026-09-18T00:00:00Z", "source": "vibe", "cost_usd": 1.50}\n'
        '{"ts": "2026-09-18T00:05:00Z", "source": "rocketreach", "cost_usd": 3.00}\n',
        encoding="utf-8",
    )

    # 3. Build summary
    summary = build_run_summary(profile, content_root=tmp_path)

    assert summary.profile == profile
    assert summary.run_id == run_id
    assert summary.mode == "full"
    assert summary.accounts_discovered == 20
    assert summary.accounts_gated == 15
    assert summary.accounts_dropped == 5
    assert summary.accounts_enriched == 12
    assert summary.accounts_ready == 10
    assert summary.accounts_generic == 2
    assert summary.connectors == {"vibe": "ok", "rocketreach": "ok"}
    assert summary.total_cost_usd == 4.50
    assert summary.cost_breakdown["vibe"] == 1.50
    assert summary.cost_breakdown["rocketreach"] == 3.00


def test_save_and_load_run_summary(tmp_path: Path) -> None:
    profile = "test-tenant"
    summary = RunSummary(
        run_id="run-xyz",
        profile=profile,
        mode="bulk",
        started_at="2026-09-18T00:00:00Z",
        completed_at="2026-09-18T00:10:00Z",
        accounts_discovered=50,
        accounts_gated=40,
        accounts_dropped=10,
        accounts_enriched=35,
        accounts_ready=30,
        accounts_generic=5,
        cost_breakdown={"vibe": 5.0, "apollo": 2.0},
        total_cost_usd=7.0,
        connectors={"vibe": "ok", "apollo": "ok"},
        errors=[],
        warnings=["generic lane share at 14%"],
        stage_durations={"discovery": 120.0, "enrichment": 240.0},
    )

    dest_file = tmp_path / "run_summary.json"
    save_run_summary(summary, dest_file)
    assert dest_file.exists()

    loaded = load_run_summary(dest_file)
    assert loaded is not None
    assert loaded.run_id == "run-xyz"
    assert loaded.accounts_discovered == 50
    assert loaded.total_cost_usd == 7.0
    assert loaded.warnings == ["generic lane share at 14%"]


def test_summary_graceful_on_missing_state(tmp_path: Path) -> None:
    # When no run_state.json exists, build_run_summary still returns a valid default summary
    summary = build_run_summary("empty-profile", content_root=tmp_path)
    assert summary.profile == "empty-profile"
    assert summary.accounts_discovered == 0
    assert summary.total_cost_usd == 0.0


def test_summary_handles_null_cost_usd(tmp_path: Path) -> None:
    profile = "test-tenant"
    costs_jsonl = tmp_path / profile / "costs.jsonl"
    costs_jsonl.parent.mkdir(parents=True, exist_ok=True)
    costs_jsonl.write_text(
        '{"ts": "2026-09-18T00:00:00Z", "tool": "firecrawl", "cost_usd": null}\n'
        '{"ts": "2026-09-18T00:01:00Z", "tool": "vibe", "cost_usd": 2.5}\n',
        encoding="utf-8",
    )
    summary = build_run_summary(profile, content_root=tmp_path)
    assert summary.total_cost_usd == 2.5
    assert summary.cost_breakdown["firecrawl"] == 0.0
    assert summary.cost_breakdown["vibe"] == 2.5
