"""Concurrency tests for prospect runs across processes."""

from __future__ import annotations

import multiprocessing
import time
from pathlib import Path

from gtm_core.run_lock import (
    RunLockBusy,
    get_lock_status,
    prospect_run_lock,
)


def _worker_hold_lock(
    profile: str, root_str: str, duration_s: float, ready_event, stop_event
) -> None:
    root = Path(root_str)
    with prospect_run_lock(profile, run_id="worker-run", content_root=root):
        ready_event.set()
        stop_event.wait(timeout=duration_s)


def test_multiprocess_lock_contention(tmp_path: Path) -> None:
    profile = "alpha-test"
    ready_event = multiprocessing.Event()
    stop_event = multiprocessing.Event()

    p = multiprocessing.Process(
        target=_worker_hold_lock,
        args=(profile, str(tmp_path), 5.0, ready_event, stop_event),
    )
    p.start()

    try:
        assert ready_event.wait(timeout=3.0) is True

        # Now attempting to acquire lock from main process should raise RunLockBusy
        status = get_lock_status(profile, content_root=tmp_path)
        assert status is not None
        assert status.run_id == "worker-run"
        assert status.pid == p.pid
        assert status.is_stale is False

        try:
            with prospect_run_lock(
                profile, run_id="main-run", content_root=tmp_path, blocking=False
            ):
                assert False, "Should have raised RunLockBusy"
        except RunLockBusy as err:
            assert err.profile == profile
            assert err.holder is not None
            assert err.holder.run_id == "worker-run"

        # Another profile CAN acquire cleanly
        with prospect_run_lock(
            "other-profile", run_id="other-run", content_root=tmp_path
        ) as other_info:
            assert other_info.profile == "other-profile"

    finally:
        stop_event.set()
        p.join(timeout=2.0)
        if p.is_alive():
            p.terminate()

    # Once worker process terminates, lock is released
    time.sleep(0.1)
    status_after = get_lock_status(profile, content_root=tmp_path)
    assert status_after is None
