"""Tests for gtm_core.run_lock (profile-scoped prospect run locks)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gtm_core.run_lock import (
    RunLockBusy,
    RunLockInfo,
    break_stale_lock,
    get_lock_status,
    is_pid_alive,
    prospect_run_lock,
)


def test_acquire_release_roundtrip(tmp_path: Path) -> None:
    profile = "test-tenant"
    run_id = "run-001"

    assert get_lock_status(profile, content_root=tmp_path) is None

    with prospect_run_lock(profile, run_id=run_id, content_root=tmp_path) as info:
        assert isinstance(info, RunLockInfo)
        assert info.profile == profile
        assert info.run_id == run_id
        assert info.pid == os.getpid()
        assert info.started_at is not None

        # Inside the lock, status should report active holder
        current = get_lock_status(profile, content_root=tmp_path)
        assert current is not None
        assert current.run_id == run_id
        assert current.pid == os.getpid()

    # After exit, lock should be released
    assert get_lock_status(profile, content_root=tmp_path) is None


def test_contention_raises_busy(tmp_path: Path) -> None:
    profile = "test-tenant"

    with prospect_run_lock(profile, run_id="run-primary", content_root=tmp_path):
        with pytest.raises(RunLockBusy) as exc_info:
            with prospect_run_lock(
                profile, run_id="run-secondary", content_root=tmp_path, blocking=False
            ):
                pass

        err = exc_info.value
        assert err.profile == profile
        assert err.holder is not None
        assert err.holder.run_id == "run-primary"
        assert err.holder.pid == os.getpid()


def test_different_profiles_do_not_block(tmp_path: Path) -> None:
    with prospect_run_lock("profile-a", run_id="run-a", content_root=tmp_path) as info_a:
        with prospect_run_lock("profile-b", run_id="run-b", content_root=tmp_path) as info_b:
            assert info_a.profile == "profile-a"
            assert info_b.profile == "profile-b"


def test_stale_pid_detection() -> None:
    # Current process PID is alive
    assert is_pid_alive(os.getpid()) is True
    # PID 99999999 is almost certainly dead on unix
    assert is_pid_alive(99999999) is False


def test_break_stale_lock(tmp_path: Path) -> None:
    profile = "test-tenant"
    run_id = "run-dead"

    # Simulate a crashed process lock by writing the lock file and sidecar directly
    lock_path = tmp_path / profile / "prospects" / ".run_lock"
    meta_path = tmp_path / profile / "prospects" / ".run_lock.json"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch()

    dead_pid = 99999999
    meta_path.write_text(
        f'{{"profile": "{profile}", "run_id": "{run_id}", "pid": {dead_pid}, "started_at": "2026-09-18T00:00:00Z"}}'
    )

    status = get_lock_status(profile, content_root=tmp_path)
    assert status is not None
    assert status.is_stale is True

    # Break stale lock
    broken = break_stale_lock(profile, content_root=tmp_path)
    assert broken is True

    # Now a new run should be able to acquire the lock cleanly
    with prospect_run_lock(profile, run_id="run-new", content_root=tmp_path) as info:
        assert info.run_id == "run-new"


def test_exception_releases_lock(tmp_path: Path) -> None:
    profile = "test-tenant"
    run_id = "run-fail"

    class CustomError(Exception):
        pass

    with pytest.raises(CustomError):
        with prospect_run_lock(profile, run_id=run_id, content_root=tmp_path):
            raise CustomError("something went wrong mid-run")

    assert get_lock_status(profile, content_root=tmp_path) is None
