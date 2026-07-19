"""Tests for ``agent.__main__._run_pipeline`` — the cron-mode entrypoint.

Three regressions discovered during the first live journey run live here:

* **Source-aware cron-day guard.** Journey and news run on different weekdays (their systemd
  timers fire Fri vs Mon). A single shared ``pipeline_cron_day`` made the off-day source skip
  every week. The guard now reads ``journey_cron_day`` (default Friday) for journey and
  ``pipeline_cron_day`` (default Monday) for news, and ``--backfill`` overrides the guard.
* **Single lock acquisition.** ``PipelineRunner.run`` already holds the per-profile lock; an
  outer wrapper in ``_run_pipeline`` re-acquired it and deadlocked (same-process ``flock``).
  The lock must be taken exactly once per run.
* **Terminal-status reduction.** ``runner.run`` returns the *manifest*; comparing it to a status
  string never matched, so the Gate 1 push never fired and a FAILED run exited 0.

The stage executor and the lock are injected/patched, so no SDK or real flock is involved.
``asyncio.run`` drives the async entrypoint without pytest-asyncio.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt

import pytest

pytest.importorskip("agent.pipeline", reason="agent.pipeline not built yet")

from agent.__main__ import _run_pipeline  # noqa: E402
from agent.pipeline import AWAITING_APPROVAL, FAILED, OK, StageOutcome  # noqa: E402

PROFILE = "example"

# Real dates — only the weekday name matters to the guard.
_MONDAY = _dt.datetime(2026, 6, 15, 9, 0)
_WEDNESDAY = _dt.datetime(2026, 6, 17, 9, 0)
_FRIDAY = _dt.datetime(2026, 6, 19, 9, 0)
_SATURDAY = _dt.datetime(2026, 6, 20, 9, 0)  # neither cadence's day


def _write_settings(cfg, **keys) -> None:
    """Write content/<profile>/settings.json with the given cadence keys."""
    import json as _json

    path = cfg.content_root / PROFILE / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(keys))


class _FixedClock:
    """Stand-in for ``agent.__main__.datetime`` whose ``now()`` is pinned."""

    def __init__(self, when: _dt.datetime) -> None:
        self._when = when

    def now(self) -> _dt.datetime:
        return self._when


def _make_executor_factory(recorder: list[str], *, fail_stage: str | None = None):
    """A ``make_executor`` stand-in: records stages, stops at Gate 1 (plan) or fails a stage."""

    def _make(cfg, profile, source="news"):
        async def _executor(stage: str, manifest: dict) -> StageOutcome:
            recorder.append(stage)
            if stage == fail_stage:
                return StageOutcome(status=FAILED, error="boom")
            if stage == "plan":
                return StageOutcome(status=AWAITING_APPROVAL, outputs=(stage,))
            return StageOutcome(status=OK, outputs=(stage,))

        return _executor

    return _make


@pytest.fixture
def harness(monkeypatch):
    """Patch ``make_executor`` and count profile-lock acquisitions on both code paths.

    Returns ``(ran, lock_calls, set_fail)``: the list of stages the executor ran, the lock
    acquisition counter, and a helper to make a later run fail at a given stage.
    """
    ran: list[str] = []
    state: dict = {"fail_stage": None}

    def _install_executor():
        monkeypatch.setattr(
            "agent.pipeline_executor.make_executor",
            _make_executor_factory(ran, fail_stage=state["fail_stage"]),
        )

    _install_executor()

    def _set_fail(stage: str) -> None:
        state["fail_stage"] = stage
        _install_executor()

    lock_calls = {"n": 0}

    @contextlib.contextmanager
    def _counting_lock(content_root, profile, *, blocking=True):
        lock_calls["n"] += 1
        yield content_root  # dirs are created by Ledgers, not by the lock

    monkeypatch.setattr("agent.pipeline.profile_lock", _counting_lock)
    monkeypatch.setattr("agent.__main__.profile_lock", _counting_lock)
    return ran, lock_calls, _set_fail


def _today(monkeypatch, when: _dt.datetime) -> None:
    monkeypatch.setattr("agent.__main__.datetime", _FixedClock(when))


# ── source-aware cron-day guard ───────────────────────────────────────────────


def test_journey_runs_on_friday(monkeypatch, cfg, harness):
    ran, lock_calls, _ = harness
    _today(monkeypatch, _FRIDAY)
    rc = asyncio.run(_run_pipeline(cfg, PROFILE, source="journey", backfill=False))
    assert rc == 0
    assert ran == ["radar", "plan"]  # passed the guard, ran to Gate 1
    assert lock_calls["n"] == 1  # lock taken exactly once


def test_journey_skips_on_monday(monkeypatch, cfg, harness):
    ran, lock_calls, _ = harness
    _today(monkeypatch, _MONDAY)
    rc = asyncio.run(_run_pipeline(cfg, PROFILE, source="journey", backfill=False))
    assert rc == 0
    assert ran == []  # guard skipped before any stage
    assert lock_calls["n"] == 0


def test_news_runs_on_monday(monkeypatch, cfg, harness):
    ran, _lock, _ = harness
    _today(monkeypatch, _MONDAY)
    asyncio.run(_run_pipeline(cfg, PROFILE, source="news", backfill=False))
    assert ran == ["radar", "plan"]


def test_news_skips_on_friday(monkeypatch, cfg, harness):
    ran, _lock, _ = harness
    _today(monkeypatch, _FRIDAY)
    asyncio.run(_run_pipeline(cfg, PROFILE, source="news", backfill=False))
    assert ran == []


def test_backfill_overrides_wrong_day(monkeypatch, cfg, harness):
    ran, _lock, _ = harness
    _today(monkeypatch, _SATURDAY)  # neither cadence's weekday
    asyncio.run(_run_pipeline(cfg, PROFILE, source="journey", backfill=True))
    assert ran == ["radar", "plan"]  # backfill ignores the guard


def test_settings_journey_cron_day_overrides_default(monkeypatch, cfg, harness):
    """settings.json is the single source of truth for cadence: setting journey_cron_day
    to Wednesday makes the journey run on Wed and SKIP its Friday default."""
    ran, _lock, _ = harness
    _write_settings(cfg, journey_cron_day="wednesday")

    _today(monkeypatch, _WEDNESDAY)
    asyncio.run(_run_pipeline(cfg, PROFILE, source="journey", backfill=False))
    assert ran == ["radar", "plan"]  # runs on the configured day

    ran.clear()
    _today(monkeypatch, _FRIDAY)
    asyncio.run(_run_pipeline(cfg, PROFILE, source="journey", backfill=False))
    assert ran == []  # the old default day no longer runs


# ── lock discipline ───────────────────────────────────────────────────────────


def test_lock_acquired_exactly_once(monkeypatch, cfg, harness):
    """Regression: ``_run_pipeline`` must not wrap ``runner.run`` in a second profile_lock."""
    _, lock_calls, _ = harness
    _today(monkeypatch, _FRIDAY)
    asyncio.run(_run_pipeline(cfg, PROFILE, source="journey", backfill=True))
    assert lock_calls["n"] == 1


# ── terminal-status reduction (Gate 1 push + failed exit code) ─────────────────


def test_gate_returns_zero(monkeypatch, cfg, harness):
    _today(monkeypatch, _FRIDAY)
    rc = asyncio.run(_run_pipeline(cfg, PROFILE, source="journey", backfill=True))
    assert rc == 0  # gated runs exit 0 (operator decides via Gate 1)


def test_failed_run_exits_nonzero(monkeypatch, cfg, harness):
    """A FAILED stage must propagate a non-zero exit so systemd doesn't see success."""
    ran, _lock, set_fail = harness
    set_fail("radar")
    _today(monkeypatch, _FRIDAY)
    rc = asyncio.run(_run_pipeline(cfg, PROFILE, source="journey", backfill=True))
    assert rc == 1
    assert ran == ["radar"]  # stopped at the failed stage, did not barrel on


# ── Gate 1 push (agent.gate_notify, moved out of cockpit/ in E-0.2) ────────────


def test_gate1_push_called_with_run_id(monkeypatch, cfg, harness):
    """A gated run must actually invoke agent.gate_notify.push_gate1 with the run's id."""
    calls: list[tuple] = []

    async def _fake_push(cfg_arg, profiles_root, profile, run_id):
        calls.append((profile, run_id))

    monkeypatch.setattr("agent.gate_notify.push_gate1", _fake_push)
    _today(monkeypatch, _FRIDAY)
    rc = asyncio.run(_run_pipeline(cfg, PROFILE, source="journey", backfill=True))
    assert rc == 0
    assert len(calls) == 1
    assert calls[0][0] == PROFILE


def test_gate1_push_failure_still_exits_zero(monkeypatch, cfg, harness):
    """A raising notifier must not change the cron exit code — push is best-effort."""

    async def _raising_push(*args, **kwargs):
        raise RuntimeError("telegram is down")

    monkeypatch.setattr("agent.gate_notify.push_gate1", _raising_push)
    _today(monkeypatch, _FRIDAY)
    rc = asyncio.run(_run_pipeline(cfg, PROFILE, source="journey", backfill=True))
    assert rc == 0
