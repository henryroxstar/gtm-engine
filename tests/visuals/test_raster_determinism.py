import multiprocessing
import os
import time
from pathlib import Path

import pytest

from gtm_core.visuals.lock import VisualsLockError, visuals_render_lock


def _hold_lock(content_root: Path, duration_s: float, lock_acquired: multiprocessing.Event):
    with visuals_render_lock(content_root, timeout_s=5.0):
        lock_acquired.set()
        time.sleep(duration_s)


def test_visuals_render_lock_serializes(tmp_path: Path):
    content_root = tmp_path / "content" / "mockcorp"
    content_root.mkdir(parents=True)
    lock_acquired = multiprocessing.Event()

    # Spawn process holding lock for 0.5s
    p = multiprocessing.Process(target=_hold_lock, args=(content_root, 0.5, lock_acquired))
    p.start()
    try:
        assert lock_acquired.wait(timeout=5.0), "Worker process failed to acquire lock in time"

        start = time.monotonic()
        with visuals_render_lock(content_root, timeout_s=5.0):
            elapsed = time.monotonic() - start
            assert elapsed >= 0.35  # Waited for worker to release
    finally:
        p.join(timeout=5.0)
        if p.is_alive():
            p.terminate()
            p.join(timeout=1.0)


def test_visuals_render_lock_timeout(tmp_path: Path):
    content_root = tmp_path / "content" / "mockcorp"
    content_root.mkdir(parents=True)
    lock_acquired = multiprocessing.Event()

    # Spawn worker holding lock for 2.0s
    p = multiprocessing.Process(target=_hold_lock, args=(content_root, 2.0, lock_acquired))
    p.start()
    try:
        assert lock_acquired.wait(timeout=5.0), "Worker process failed to acquire lock in time"

        start = time.monotonic()
        with pytest.raises(VisualsLockError, match="Timed out after 0.2s"):
            with visuals_render_lock(content_root, timeout_s=0.2):
                pass
        elapsed = time.monotonic() - start
        assert 0.15 <= elapsed <= 1.0
    finally:
        p.join(timeout=5.0)
        if p.is_alive():
            p.terminate()
            p.join(timeout=1.0)


def test_visuals_render_lock_records_pid(tmp_path: Path):
    content_root = tmp_path / "content" / "mockcorp"
    with visuals_render_lock(content_root, timeout_s=5.0) as lock_path:
        assert lock_path.is_file()
        content = lock_path.read_text(encoding="utf-8").strip()
        assert content == str(os.getpid())


def test_visuals_render_lock_unlinks_symlink(tmp_path: Path):
    content_root = tmp_path / "content" / "mockcorp"
    locks_dir = content_root / ".locks"
    locks_dir.mkdir(parents=True)
    sensitive_target = tmp_path / "sensitive.txt"
    sensitive_target.write_text("SENSITIVE DATA")

    lock_file = locks_dir / "visuals-render.lock"
    lock_file.symlink_to(sensitive_target)
    assert lock_file.is_symlink()

    with visuals_render_lock(content_root, timeout_s=5.0) as acquired_lock:
        assert not acquired_lock.is_symlink()
        assert acquired_lock.is_file()
        # Sensitive target was not overwritten with pid
        assert sensitive_target.read_text() == "SENSITIVE DATA"
