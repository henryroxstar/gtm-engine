"""Tests for the deterministic stage runner (agent.pipeline) — resume-from-failure.

The runner finally *consumes* the RunManifest the schema was built to carry: a run that died
mid-pipeline resumes from the first non-complete stage instead of restarting. The stage work is
an injectable async callable, so the orchestration logic is tested with a fake (no SDK, no
network). ``asyncio.run`` drives the async ``run`` without pytest-asyncio.
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("agent.pipeline", reason="agent.pipeline not built yet")

from agent.pipeline import (  # noqa: E402
    AWAITING_APPROVAL,
    FAILED,
    OK,
    STAGES,
    PipelineRunner,
    StageOutcome,
    new_manifest,
    resume_index,
    terminal_status,
)

PROFILE = "example"


# ── terminal_status (pure) ────────────────────────────────────────────────────


def _manifest_with(**status_by_stage):
    """A manifest with each stage set to ``OK`` unless overridden by name."""
    m = new_manifest("r", "cron", PROFILE)
    for s in m["stages"]:
        s["status"] = status_by_stage.get(s["name"], OK)
    return m


def test_terminal_status_ok_when_all_complete():
    assert terminal_status(_manifest_with()) == OK


def test_terminal_status_gate_when_plan_awaiting():
    m = _manifest_with(
        plan=AWAITING_APPROVAL, research="pending", studio="pending", publish="pending"
    )
    assert terminal_status(m) == AWAITING_APPROVAL


def test_terminal_status_failed_when_any_stage_failed():
    assert terminal_status(_manifest_with(research=FAILED)) == FAILED


def test_terminal_status_failed_beats_gate():
    # A failed stage must win over a gate so the cron run surfaces the failure (exit non-zero).
    m = _manifest_with(plan=AWAITING_APPROVAL, research=FAILED)
    assert terminal_status(m) == FAILED


# ── resume_index (pure) ───────────────────────────────────────────────────────


def test_resume_index_fresh_manifest_starts_at_zero():
    m = new_manifest("r1", "cron", PROFILE)
    assert resume_index(m) == 0


def test_resume_index_skips_completed_prefix():
    m = new_manifest("r1", "cron", PROFILE)
    m["stages"][0]["status"] = OK  # radar done
    m["stages"][1]["status"] = "skipped"  # plan skipped (also "complete")
    assert resume_index(m) == 2  # resume at research


def test_resume_index_resumes_at_failed_stage():
    m = new_manifest("r1", "cron", PROFILE)
    m["stages"][0]["status"] = OK
    m["stages"][1]["status"] = FAILED  # plan failed → resume there, not from the top
    assert resume_index(m) == 1


def test_resume_index_all_done_returns_len():
    m = new_manifest("r1", "cron", PROFILE)
    for s in m["stages"]:
        s["status"] = OK
    assert resume_index(m) == len(STAGES)


# ── runner: happy path ────────────────────────────────────────────────────────


def _recording_stage_fn(outcomes=None):
    """An async stage callable that records which stages it runs and returns scripted outcomes."""
    ran: list[str] = []
    outcomes = outcomes or {}

    async def _run_stage(stage, manifest):
        ran.append(stage)
        return outcomes.get(stage, StageOutcome(status=OK))

    return _run_stage, ran


def test_runner_runs_all_stages_and_persists_manifest(cfg):
    runner = PipelineRunner(cfg, PROFILE)
    stage_fn, ran = _recording_stage_fn()
    manifest = asyncio.run(runner.run("r-1", "cron", stage_fn))

    assert ran == list(STAGES)  # ran every stage in order
    assert all(s["status"] == OK for s in manifest["stages"])

    # And it was persisted under content/<profile>/runs/<run_id>.json.
    path = cfg.content_root / PROFILE / "runs" / "r-1.json"
    assert path.is_file()
    assert json.loads(path.read_text())["stages"][0]["status"] == OK


# ── runner: stops on failure, then RESUMES from the failed stage ──────────────


def test_runner_stops_on_failed_stage_then_resumes(cfg):
    runner = PipelineRunner(cfg, PROFILE)

    # First run: research fails → run stops with radar/plan ok, research failed.
    stage_fn1, ran1 = _recording_stage_fn({"research": StageOutcome(status=FAILED, error="boom")})
    m1 = asyncio.run(runner.run("r-2", "cron", stage_fn1))
    assert ran1 == ["radar", "plan", "research"]  # did not barrel on to studio/publish
    by_name = {s["name"]: s["status"] for s in m1["stages"]}
    assert by_name["research"] == FAILED
    assert by_name["studio"] == "pending" and by_name["publish"] == "pending"

    # Second run (same run_id): a healthy callable resumes at research — NOT radar.
    stage_fn2, ran2 = _recording_stage_fn()
    m2 = asyncio.run(runner.run("r-2", "cron", stage_fn2))
    assert ran2 == ["research", "studio", "publish"]  # resumed, did not repeat radar/plan
    assert all(s["status"] == OK for s in m2["stages"])


def test_runner_stops_and_persists_on_human_gate(cfg):
    runner = PipelineRunner(cfg, PROFILE)
    stage_fn, ran = _recording_stage_fn({"plan": StageOutcome(status=AWAITING_APPROVAL)})
    m = asyncio.run(runner.run("r-3", "cron", stage_fn))

    assert ran == ["radar", "plan"]  # paused at the plan gate
    by_name = {s["name"]: s["status"] for s in m["stages"]}
    assert by_name["plan"] == AWAITING_APPROVAL
    assert by_name["research"] == "pending"


def test_runner_records_stage_exception_as_failed_stage(cfg):
    runner = PipelineRunner(cfg, PROFILE)

    async def _boom(stage, manifest):
        if stage == "plan":
            raise RuntimeError("kaboom")
        return StageOutcome(status=OK)

    m = asyncio.run(runner.run("r-4", "cron", _boom))
    by_name = {s["name"]: s["status"] for s in m["stages"]}
    assert by_name["radar"] == OK
    assert by_name["plan"] == FAILED
    assert "kaboom" in (next(s for s in m["stages"] if s["name"] == "plan").get("error") or "")
