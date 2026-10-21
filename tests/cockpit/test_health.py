"""F7 — real liveness: heartbeat truth, watchdog, never-mid-dispatch, executed health check.

Test plan F7-1..F7-4. Real files, real threads, a real loop that is really blocked; the only injected
piece is the watchdog's exit function (a test cannot ``os._exit``) — and F7-4 runs the actual
``python -m cockpit.health`` the compose file points at.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent import inflight
from cockpit import health

REPO = Path(__file__).resolve().parents[2]


def _age(path: Path, seconds: float) -> None:
    t = time.time() - seconds
    os.utime(path, (t, t))


# ── F7-1 heartbeat truth ─────────────────────────────────────────────────────


def _app(running: bool):
    return SimpleNamespace(updater=SimpleNamespace(running=running))


def test_f7_1_heartbeat_advances_only_while_the_updater_runs(tmp_path):
    hb = tmp_path / "hb"
    app = _app(True)

    async def scenario():
        task = asyncio.create_task(health.heartbeat_loop(app, hb, interval_s=0.02))
        await asyncio.sleep(0.15)
        assert hb.exists()
        app.updater.running = False  # the loop is alive but polling is not
        await asyncio.sleep(0.06)
        frozen = hb.stat().st_mtime
        await asyncio.sleep(0.15)
        assert hb.stat().st_mtime == frozen  # no touch while not polling
        app.updater.running = True
        await asyncio.sleep(0.12)
        assert hb.stat().st_mtime > frozen  # and it resumes when polling does
        task.cancel()

    asyncio.run(scenario())


def test_f7_1_a_blocked_event_loop_stops_the_heartbeat(tmp_path):
    hb = tmp_path / "hb"
    app = _app(True)

    async def scenario():
        task = asyncio.create_task(health.heartbeat_loop(app, hb, interval_s=0.02))
        await asyncio.sleep(0.1)
        before = hb.stat().st_mtime
        time.sleep(0.4)  # a handler that blocks the loop: nothing else on it can run
        assert hb.stat().st_mtime == before
        task.cancel()

    asyncio.run(scenario())


def test_f7_1_there_is_no_job_queue_dependency_in_the_installed_ptb():
    # E16: the heartbeat must not rely on JobQueue — the installed PTB does not ship one.
    from telegram.ext import Application

    app = Application.builder().token("123:abc").build()
    assert app.job_queue is None


# ── F7-2 watchdog ────────────────────────────────────────────────────────────


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _dog(tmp_path, *, limit=120, grace=60, cap=600, clock=None):
    exits: list[int] = []
    hb = tmp_path / "hb"
    clock = clock or Clock()
    dog = health.Watchdog(
        hb,
        limit_s=limit,
        grace_s=grace,
        inflight_cap_s=cap,
        exit_fn=exits.append,
        clock=clock,
        wall=clock,
    )
    return dog, hb, exits, clock


def _touch_at(hb: Path, t: float) -> None:
    hb.touch()
    os.utime(hb, (t, t))


def test_f7_2_stale_heartbeat_exits_non_zero_but_fresh_never_does(tmp_path):
    dog, hb, exits, clock = _dog(tmp_path)
    _touch_at(hb, clock.t - 30)
    assert dog.check_once() is False and exits == []
    _touch_at(hb, clock.t - 121)
    assert dog.check_once() is True and exits and exits[0] != 0


def test_f7_2_disabled_with_zero(tmp_path):
    dog, hb, exits, clock = _dog(tmp_path, limit=0)
    _touch_at(hb, clock.t - 99999)
    assert dog.check_once() is False and exits == []


def test_f7_2_missing_file_is_stale_only_after_the_boot_grace_period(tmp_path):
    dog, hb, exits, clock = _dog(tmp_path, grace=60)
    clock.t += 30
    assert dog.check_once() is False  # still booting
    clock.t += 61
    assert dog.check_once() is True and exits


# ── F7-3 never mid-dispatch ──────────────────────────────────────────────────


def test_f7_3_a_stale_heartbeat_does_not_kill_an_in_flight_dispatch(tmp_path):
    dog, hb, exits, clock = _dog(tmp_path, cap=600)
    _touch_at(hb, clock.t - 130)  # loop blocked for 130 s
    with inflight.track():
        assert dog.check_once() is False and exits == []
        clock.t += 300
        assert dog.check_once() is False and exits == []  # still protected inside the cap
        clock.t += 301  # past the hard 10-minute cap
        assert dog.check_once() is True and exits
    # and once the dispatch has finished, a stale heartbeat is acted on again
    other = tmp_path / "b"
    other.mkdir()
    dog2, hb2, exits2, clock2 = _dog(other)
    _touch_at(hb2, clock2.t - 130)
    assert dog2.check_once() is True


def test_f7_3_the_watchdog_exits_promptly_after_the_dispatch_ends(tmp_path):
    dog, hb, exits, clock = _dog(tmp_path)
    _touch_at(hb, clock.t - 130)
    with inflight.track():
        assert dog.check_once() is False
    assert dog.check_once() is True


def test_f7_3_sigterm_waits_for_in_flight_dispatches_up_to_the_timeout():
    assert inflight.wait_idle(0.05) is True  # nothing running
    done = threading.Event()

    def worker():
        with inflight.track():
            done.wait(2)

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.05)
    assert inflight.count() == 1
    assert inflight.wait_idle(0.1) is False  # timed out while still running
    done.set()
    t.join()
    assert inflight.wait_idle(1.0) is True


def test_inflight_counter_is_exception_safe():
    with pytest.raises(RuntimeError), inflight.track():
        raise RuntimeError("boom")
    assert inflight.count() == 0


# ── F7-4 the health check is EXECUTED, not read ──────────────────────────────


def _run_health(root: Path, **env) -> int:
    e = {**os.environ, "GTM_CONTENT_ROOT": str(root), **env}
    return subprocess.run(
        [sys.executable, "-m", "cockpit.health"], cwd=REPO, env=e, capture_output=True
    ).returncode


def test_f7_4_health_check_fails_when_stale_and_passes_when_fresh(tmp_path):
    hb = tmp_path / "_system" / "cockpit-heartbeat"
    hb.parent.mkdir(parents=True)
    hb.touch()
    assert _run_health(tmp_path) == 0
    _age(hb, 300)
    assert _run_health(tmp_path) == 1
    hb.unlink()
    assert _run_health(tmp_path) == 1  # a missing heartbeat is unhealthy (start_period covers boot)


def test_f7_4_compose_health_check_is_the_command_we_tested():
    import yaml

    for rel in ("docker-compose.yml", "oss/overlays/docker-compose.yml"):
        path = REPO / rel
        if not path.exists():
            continue
        svc = yaml.safe_load(path.read_text())["services"]["gtm-agent"]
        assert svc["healthcheck"]["test"] == ["CMD", "python", "-m", "cockpit.health"], rel
        assert svc["stop_grace_period"] == "90s", rel
        assert any(
            isinstance(v, dict)
            and v.get("target") == "/home/gtm/.claude/projects"
            and v.get("type") == "volume"
            for v in svc["volumes"]
        ), f"{rel}: only the CLI transcripts directory may be persisted"
        targets = [v["target"] for v in svc["volumes"] if isinstance(v, dict)]
        assert (
            "/home/gtm/.claude" not in targets and "/home/gtm" not in targets
        )  # no credentials volume


def test_f1_9_project_settings_carry_the_transcript_retention_the_cli_requires():
    """With setting_sources=['project'] the CLI skips its retention sweep unless an ENABLED source
    provides cleanupPeriodDays (observed live: tests/live_sdk). It must outlive the 7-day resume window."""
    import json

    days = json.loads((REPO / ".claude" / "settings.json").read_text())["cleanupPeriodDays"]
    assert isinstance(days, int) and 7 <= days <= 90
    dockerfile = (REPO / "Dockerfile").read_text()
    assert (
        "install -d -o gtm -g gtm /home/gtm/.claude/projects" in dockerfile
    )  # volume is writable by uid 10001
