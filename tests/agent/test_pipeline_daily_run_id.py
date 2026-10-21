"""F5 — a scheduled pipeline run has a DETERMINISTIC id, so re-running it resumes (always-on hardening PRD).

Until now ``--pipeline`` minted ``r-YYYYMMDD-HHMM-<source>`` (to the minute), so a run that crashed was never
resumed — the next invocation started a brand-new run id and redid completed work. The id is now
``r-YYYYMMDD-<source>`` (UTC date): re-running the same day finds the same manifest and the existing per-node
manifest resume does the rest. No automatic retry is added (PRD D10) — a failure alerts, and a human re-runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import json
from pathlib import Path

import pytest

pytest.importorskip("agent.pipeline", reason="agent.pipeline not built yet")

from agent.__main__ import _run_pipeline  # noqa: E402
from agent.pipeline import AWAITING_APPROVAL, FAILED, OK, StageOutcome  # noqa: E402

PROFILE = "example"
UTC = _dt.UTC
REPO = Path(__file__).resolve().parents[2]


class _Clock:
    """Stands in for ``agent.__main__.datetime``: ``now()`` local (naive) and ``now(UTC)`` aware."""

    def __init__(self, when_utc: _dt.datetime) -> None:
        self.when_utc = when_utc

    def now(self, tz=None):
        if tz is None:  # the cron-day guard reads local time; tests pin local == UTC
            return self.when_utc.replace(tzinfo=None)
        return self.when_utc.astimezone(tz)


@pytest.fixture
def world(monkeypatch, cfg):
    """(ran, clock, set_fail, pushes): the stages executed, a settable UTC clock, a stage-failure switch,
    and the Gate-1 pushes."""
    ran: list[str] = []
    state = {"fail": None}
    pushes: list[str] = []
    clock = _Clock(_dt.datetime(2026, 6, 15, 9, 0, tzinfo=UTC))  # a Monday

    def make(cfg_, profile, source="news"):
        async def executor(stage, manifest):
            ran.append(stage)
            if stage == state["fail"]:
                return StageOutcome(status=FAILED, error="boom")
            if stage == "plan":
                return StageOutcome(status=AWAITING_APPROVAL, outputs=(stage,))
            return StageOutcome(status=OK, outputs=(stage,))

        return executor

    @contextlib.contextmanager
    def lock(content_root, profile, *, blocking=True):
        yield content_root

    async def push(cfg_, profiles_root, profile, run_id):
        pushes.append(run_id)

    monkeypatch.setattr("agent.pipeline_executor.make_executor", make)
    monkeypatch.setattr("agent.pipeline.profile_lock", lock)
    monkeypatch.setattr("agent.__main__.profile_lock", lock)
    monkeypatch.setattr("agent.gate_notify.push_gate1", push)
    monkeypatch.setattr("agent.__main__.datetime", clock)

    def set_fail(stage):
        state["fail"] = stage

    return ran, clock, set_fail, pushes


def _run(cfg, **kw):
    return asyncio.run(_run_pipeline(cfg, PROFILE, source="news", **kw))


def _manifests(cfg) -> list[str]:
    d = cfg.content_root / PROFILE / "runs"
    return sorted(p.stem for p in d.glob("*.json")) if d.is_dir() else []


# ── F5-1 the id ──────────────────────────────────────────────────────────────


def test_f5_1_the_run_id_is_the_utc_date_and_source_and_nothing_finer(cfg, world):
    _run(cfg)
    assert _manifests(cfg) == ["r-20260615-news"]


def test_f5_1_running_twice_in_a_day_resumes_the_same_run(cfg, world):
    ran, clock, set_fail, pushes = world
    _run(cfg)
    clock.when_utc += _dt.timedelta(hours=6)  # same UTC day, later
    ran.clear()
    _run(cfg)
    assert _manifests(cfg) == ["r-20260615-news"]  # one run, not two


def test_f5_1_rerun_makes_a_deliberate_second_run_with_a_numbered_id(cfg, world):
    _run(cfg)
    _run(cfg, rerun=True)
    _run(cfg, rerun=True)
    assert _manifests(cfg) == ["r-20260615-news", "r-20260615-news-2", "r-20260615-news-3"]


def test_f5_1_the_utc_date_rolls_over_at_midnight_utc_not_local_midnight(cfg, world):
    """A run at 23:58 UTC and its re-run at 00:03 UTC are DIFFERENT ids, by rule (stated and tested)."""
    _, clock, _, _ = world
    clock.when_utc = _dt.datetime(2026, 6, 15, 23, 58, tzinfo=UTC)
    _run(cfg, backfill=True)
    clock.when_utc = _dt.datetime(2026, 6, 16, 0, 3, tzinfo=UTC)
    _run(cfg, backfill=True)
    ids = _manifests(cfg)
    assert any("20260615" in i for i in ids) and any("20260616" in i for i in ids)


def test_f5_1_backfill_never_collides_with_the_daily_run(cfg, world):
    _run(cfg)
    _run(cfg, backfill=True)
    assert _manifests(cfg) == ["r-20260615-news", "r-20260615-news-backfill"]


def test_f5_1_sources_do_not_collide(cfg, world):
    _run(cfg)
    asyncio.run(_run_pipeline(cfg, PROFILE, source="journey", backfill=True))
    assert "r-20260615-journey-backfill" in _manifests(cfg) and "r-20260615-news" in _manifests(cfg)


# ── F5-2 states ──────────────────────────────────────────────────────────────


def test_f5_2_parked_at_a_gate_exits_zero_and_pushes_no_second_gate(cfg, world, capsys):
    ran, _, _, pushes = world
    assert _run(cfg) == 0
    assert pushes == ["r-20260615-news"] and ran == ["radar", "plan"]
    ran.clear()
    assert _run(cfg) == 0
    assert ran == [] and pushes == ["r-20260615-news"]  # nothing re-ran, no second push
    assert "awaiting gate" in capsys.readouterr().out


def test_f5_2_a_complete_run_says_it_already_ran_today(cfg, world, capsys):
    ran, _, _, _ = world
    d = cfg.content_root / PROFILE / "runs"
    d.mkdir(parents=True)
    (d / "r-20260615-news.json").write_text(
        json.dumps(
            {
                "run_id": "r-20260615-news",
                "profile": PROFILE,
                "stages": [
                    {"name": n, "status": "ok"}
                    for n in ("radar", "plan", "research", "studio", "publish")
                ],
            }
        )
    )
    assert _run(cfg) == 0
    assert ran == []
    assert "already ran today" in capsys.readouterr().out


def test_f5_2_a_failed_run_resumes_at_the_failed_node_and_does_not_redo_completed_ones(cfg, world):
    ran, _, set_fail, _ = world
    set_fail("plan")
    assert _run(cfg) == 1  # radar ok, plan FAILED
    assert ran == ["radar", "plan"]
    ran.clear()
    set_fail(None)
    assert _run(cfg) == 0  # same id: resumes
    assert ran == ["plan"]  # radar's completed output was NOT regenerated
    assert _manifests(cfg) == ["r-20260615-news"]


def test_f5_2_a_crashed_run_with_a_stage_left_running_resumes(cfg, world):
    ran, _, _, _ = world
    d = cfg.content_root / PROFILE / "runs"
    d.mkdir(parents=True)
    (d / "r-20260615-news.json").write_text(
        json.dumps(
            {
                "run_id": "r-20260615-news",
                "profile": PROFILE,
                "stages": [
                    {"name": "radar", "status": "ok"},
                    {"name": "plan", "status": "running"},  # the process died here
                    {"name": "research", "status": "pending"},
                    {"name": "studio", "status": "pending"},
                    {"name": "publish", "status": "pending"},
                ],
            }
        )
    )
    assert _run(cfg) == 0
    assert ran == ["plan"]


def test_f5_2_a_corrupt_manifest_starts_fresh_rather_than_blocking_the_day(cfg, world):
    ran, _, _, _ = world
    d = cfg.content_root / PROFILE / "runs"
    d.mkdir(parents=True)
    (d / "r-20260615-news.json").write_text("{not json")
    assert _run(cfg) == 0
    assert ran == ["radar", "plan"]


# ── F5-3 no automatic retry ──────────────────────────────────────────────────


def test_f5_3_no_pipeline_unit_restarts_on_failure_and_the_budget_guard_precedes_each_run():
    for unit in sorted((REPO / "systemd").glob("gtm-pipeline-*.service")):
        text = unit.read_text()
        restarts = [ln for ln in text.splitlines() if ln.strip().startswith("Restart=")]
        assert all(ln.split("=", 1)[1].strip() == "no" for ln in restarts), unit.name
        assert "ExecStartPre=" in text and "ExecStart=" in text, (
            unit.name
        )  # §R2 guard precedes the run
        assert (
            text.index("ExecStartPre=") < text.index("ExecStart=doppler")
            if "ExecStart=doppler" in text
            else True
        )


def test_f5_3_a_deterministic_failure_is_exactly_one_attempt(cfg, world):
    ran, _, set_fail, _ = world
    set_fail("radar")
    assert _run(cfg) == 1
    assert ran == ["radar"]  # one attempt; nothing in the process retries it
