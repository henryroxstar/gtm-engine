"""Tests for the per-profile cross-process advisory lock (agent.locks).

The lock serialises a profile's state mutations across the cron ``hermes-brain`` and the Telegram
``hermes-cockpit`` (which share the ``content/`` volume). Here we assert: the lock is created under
the profile tree; a second NON-blocking acquire while held raises ``LockBusy``; and after release a
fresh acquire succeeds. Uses two separate open file descriptions (the contextmanager opens fresh
each call), which is exactly the cross-process scenario flock guards.
"""

from __future__ import annotations

import pytest

pytest.importorskip("agent.locks", reason="agent.locks not built yet")

from agent.locks import LockBusy, profile_lock  # noqa: E402

PROFILE = "example"


def test_lock_created_under_profile_tree(tmp_path):
    with profile_lock(tmp_path, PROFILE) as lock_path:
        assert lock_path == tmp_path / PROFILE / ".lock"
        assert lock_path.is_file()


def test_second_nonblocking_acquire_while_held_raises(tmp_path):
    with profile_lock(tmp_path, PROFILE):
        with pytest.raises(LockBusy):
            with profile_lock(tmp_path, PROFILE, blocking=False):
                pass  # pragma: no cover — should not enter


def test_lock_released_after_block(tmp_path):
    with profile_lock(tmp_path, PROFILE):
        pass
    # Released → a fresh non-blocking acquire now succeeds.
    with profile_lock(tmp_path, PROFILE, blocking=False) as lock_path:
        assert lock_path.is_file()


def test_distinct_profiles_do_not_contend(tmp_path):
    # Holding example's lock must not block example2's (separate lock files).
    with profile_lock(tmp_path, "example"):
        with profile_lock(tmp_path, "example2", blocking=False) as other:
            assert other == tmp_path / "example2" / ".lock"
