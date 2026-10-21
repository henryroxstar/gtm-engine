"""Behavioural tests for systemd/notify.sh's failure-alert rate limit.

Runs the real script with a stub ``curl`` on PATH that records each would-be Telegram
send, so these assert what an operator's phone actually receives — not what the source
looks like. The bug being fenced off (2026-09-01): ``gtm-optout-watch`` failed on all
six of its daily runs for as long as its connector key was unwired, and six identical
pings a day is precisely how a person learns to swipe this channel away — taking the
next real outage with it.

The rate limit caps the NOTIFICATION, never the record: journalctl and each job's own
ledger rows still carry every run.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

NOTIFY = Path(__file__).resolve().parent.parent / "systemd" / "notify.sh"
if not NOTIFY.exists():
    # systemd/ is excluded from the OSS carve by design (VPS deploy surface).
    pytest.skip("systemd/ not present (private deploy surface)", allow_module_level=True)


@pytest.fixture
def notify(tmp_path):
    """Return ``run(job, result, **env)`` plus the list of captured pings."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "curl.log"
    stub = bin_dir / "curl"
    stub.write_text(f'#!/usr/bin/env bash\necho "$@" >> {log}\n', encoding="utf-8")
    stub.chmod(0o755)

    state = tmp_path / "state"

    def run(job: str, result: str, **env: str) -> subprocess.CompletedProcess:
        merged = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_ALLOWED_CHAT_ID": "42",
            "GTM_NOTIFY_STATE_DIR": str(state),
            **env,
        }
        return subprocess.run(
            ["bash", str(NOTIFY), job, result], env=merged, capture_output=True, text=True
        )

    def pings() -> list[str]:
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    return run, pings


def test_first_failure_always_alerts(notify):
    run, pings = notify
    assert run("gtm-optout-watch", "exit-code").returncode == 0
    assert len(pings()) == 1
    assert "still failing" not in pings()[0]


def test_a_repeat_failure_within_the_window_is_not_re_sent(notify):
    """The exact storm from the incident: same job, same result, every four hours."""
    run, pings = notify
    for _ in range(6):
        assert run("gtm-optout-watch", "exit-code").returncode == 0
    assert len(pings()) == 1, f"expected one ping for six identical failures, got {pings()}"


def test_the_window_elapsing_re_alerts_and_says_it_is_still_failing(notify, tmp_path):
    """Suppression must not become amnesia — a still-broken job has to resurface, and
    say so, rather than being quietly dropped forever after the first ping.

    Backdates the stamp instead of sleeping: the elapsed-window branch is what is under
    test, not the wall clock.
    """
    run, pings = notify
    run("gtm-optout-watch", "exit-code")
    assert len(pings()) == 1

    stamp = next((tmp_path / "state").glob("*.last"))
    _, result = stamp.read_text(encoding="utf-8").split()
    stamp.write_text(f"{int(time.time()) - 999_999} {result}\n", encoding="utf-8")

    assert run("gtm-optout-watch", "exit-code").returncode == 0
    assert len(pings()) == 2
    assert "still failing" in pings()[-1]


def test_a_changed_result_alerts_immediately(notify):
    """A job that starts failing a NEW way is new information, cooldown or not."""
    run, pings = notify
    run("gtm-optout-watch", "exit-code")
    run("gtm-optout-watch", "timeout")
    assert len(pings()) == 2


def test_a_clean_run_clears_the_stamp_so_the_next_failure_alerts(notify):
    run, pings = notify
    run("gtm-optout-watch", "exit-code")
    run("gtm-optout-watch", "success")
    run("gtm-optout-watch", "exit-code")
    assert len(pings()) == 2, "a failure after a recovery must never be suppressed"


def test_a_clean_run_never_pings(notify):
    run, pings = notify
    for result in ("success", "0"):
        assert run("gtm-backup", result).returncode == 0
    assert pings() == []


def test_two_jobs_failing_do_not_suppress_each_other(notify):
    run, pings = notify
    run("gtm-optout-watch", "exit-code")
    run("gtm-prospect", "exit-code")
    assert len(pings()) == 2


def test_an_unwritable_state_dir_alerts_rather_than_going_silent(notify):
    """Fail OPEN: if the cooldown can't keep state, the alert still goes out. A rate
    limit that silences alerts when its own storage breaks is worse than no limit."""
    run, pings = notify
    assert run("gtm-optout-watch", "exit-code", GTM_NOTIFY_STATE_DIR="/proc/nope").returncode == 0
    assert len(pings()) == 1


def test_a_corrupt_stamp_alerts_rather_than_suppressing(notify, tmp_path):
    run, pings = notify
    run("gtm-optout-watch", "exit-code")
    stamp = next((tmp_path / "state").glob("*.last"))
    stamp.write_text("garbage", encoding="utf-8")
    assert run("gtm-optout-watch", "exit-code").returncode == 0
    assert len(pings()) == 2


def test_the_ping_still_carries_job_result_and_the_journalctl_hint(notify):
    run, pings = notify
    run("gtm-optout-watch", "exit-code")
    body = pings()[0]
    assert "gtm-optout-watch" in body
    assert "exit-code" in body
    assert "journalctl" in body
