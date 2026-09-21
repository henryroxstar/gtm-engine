"""Tests for gtm_core.run_state (prospect run state machine & resumption)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core.run_state import (
    STAGES,
    RunState,
    load_run_state,
    save_run_state,
)


def test_new_run_state_initialization() -> None:
    state = RunState.new(profile="test-profile", mode="full", run_id="run-123")
    assert state.profile == "test-profile"
    assert state.mode == "full"
    assert state.run_id == "run-123"
    assert state.completed_at is None
    assert len(state.stages) == len(STAGES)
    for name in STAGES:
        assert name in state.stages
        assert state.stages[name].status == "pending"
        assert state.stages[name].metrics == {}
        assert state.stages[name].error is None

    # Fresh run starts from first stage
    assert state.resume_from() == STAGES[0]


def test_stage_progression_and_resumption() -> None:
    state = RunState.new(profile="test-profile", mode="full")

    # Start init
    state.start_stage("init")
    assert state.stages["init"].status == "running"
    assert state.stages["init"].started_at is not None

    # Complete init
    state.complete_stage("init", metrics={"preflight": "ok"})
    assert state.stages["init"].status == "completed"
    assert state.stages["init"].completed_at is not None
    assert state.stages["init"].metrics == {"preflight": "ok"}
    assert state.resume_from() == "discovery"

    # Complete discovery
    state.start_stage("discovery")
    state.complete_stage("discovery", metrics={"accounts_discovered": 25, "credits_spent": 1.5})
    assert state.stages["discovery"].status == "completed"
    assert state.resume_from() == "signal_hunt"

    # Fail signal_hunt
    state.start_stage("signal_hunt")
    state.fail_stage("signal_hunt", error="Web rate limit exceeded")
    assert state.stages["signal_hunt"].status == "failed"
    assert state.stages["signal_hunt"].error == "Web rate limit exceeded"

    # resume_from should point to the failed stage
    assert state.resume_from() == "signal_hunt"


def test_all_stages_completed() -> None:
    state = RunState.new(profile="test-profile")
    for name in STAGES:
        state.start_stage(name)
        state.complete_stage(name)

    assert state.resume_from() is None
    assert state.is_completed is True


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    state_file = tmp_path / "run_state.json"
    state = RunState.new(profile="test-profile", mode="bulk", run_id="run-bulk-99")
    state.start_stage("init")
    state.complete_stage("init", metrics={"checks": 4})
    state.start_stage("discovery")
    state.complete_stage("discovery", metrics={"count": 100})

    save_run_state(state, state_file)
    assert state_file.exists()

    loaded = load_run_state(state_file)
    assert loaded is not None
    assert loaded.profile == "test-profile"
    assert loaded.mode == "bulk"
    assert loaded.run_id == "run-bulk-99"
    assert loaded.stages["init"].status == "completed"
    assert loaded.stages["discovery"].metrics == {"count": 100}
    assert loaded.stages["signal_hunt"].status == "pending"
    assert loaded.resume_from() == "signal_hunt"


def test_atomic_write_preserves_on_failure(tmp_path: Path) -> None:
    state_file = tmp_path / "run_state.json"
    state = RunState.new(profile="test-profile", run_id="run-1")
    save_run_state(state, state_file)

    initial_content = state_file.read_text(encoding="utf-8")
    assert "run-1" in initial_content


def test_invalid_stage_names_rejected() -> None:
    state = RunState.new(profile="test-profile")
    with pytest.raises(ValueError, match="Unknown stage"):
        state.start_stage("invalid_stage_name")

    with pytest.raises(ValueError, match="Unknown stage"):
        state.complete_stage("invalid_stage_name")

    with pytest.raises(ValueError, match="Unknown stage"):
        state.fail_stage("invalid_stage_name", "some error")


def test_get_or_create_run_state_expiration(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    from gtm_core.run_state import get_or_create_run_state

    # 1. Stale run state (3 days old) with default 48h limit -> starts fresh
    profile_stale = "test-stale-profile"
    old_time = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    old_state = RunState.new(profile=profile_stale, run_id="run-old")
    old_state.started_at = old_time
    old_state.start_stage("discovery")

    prospects_dir = tmp_path / profile_stale / "prospects"
    prospects_dir.mkdir(parents=True, exist_ok=True)
    save_run_state(old_state, prospects_dir / "run_state.json")

    fresh, resumed = get_or_create_run_state(profile_stale, content_root=tmp_path)
    assert resumed is False
    assert fresh.run_id != "run-old"

    # 2. Recent run state (10 hours old) with default 48h limit -> resumes
    profile_recent = "test-recent-profile"
    recent_time = (datetime.now(UTC) - timedelta(hours=10)).isoformat()
    recent_state = RunState.new(profile=profile_recent, run_id="run-recent")
    recent_state.started_at = recent_time
    recent_state.start_stage("discovery")

    prospects_recent_dir = tmp_path / profile_recent / "prospects"
    prospects_recent_dir.mkdir(parents=True, exist_ok=True)
    save_run_state(recent_state, prospects_recent_dir / "run_state.json")

    resumed_state, was_resumed = get_or_create_run_state(profile_recent, content_root=tmp_path)
    assert was_resumed is True
    assert resumed_state.run_id == "run-recent"
