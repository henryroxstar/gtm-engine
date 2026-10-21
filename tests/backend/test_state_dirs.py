"""MM10: the backend refuses to boot, once and legibly, when the state it owns is not writable."""

from __future__ import annotations

import os
import stat

import pytest

from backend.state_dirs import StateDirError, assert_state_dirs_writable

pytestmark = pytest.mark.skipif(
    hasattr(os, "getuid") and os.getuid() == 0, reason="root can write anywhere: nothing to refuse"
)


def test_a_writable_directory_passes_and_a_missing_one_is_created(tmp_path):
    existing = tmp_path / "present"
    existing.mkdir()
    missing = tmp_path / "a" / "b"
    assert_state_dirs_writable([existing, missing])
    assert missing.is_dir()


def test_a_directory_the_user_cannot_write_refuses_with_the_command_that_fixes_it(tmp_path):
    locked = tmp_path / "workspaces"
    locked.mkdir()
    locked.chmod(
        stat.S_IRUSR | stat.S_IXUSR
    )  # a root-owned volume, as the unprivileged user sees it
    try:
        with pytest.raises(StateDirError) as err:
            assert_state_dirs_writable([locked])
        msg = str(err.value)
        assert str(locked) in msg and "chown -R 10001:10001" in msg
    finally:
        locked.chmod(stat.S_IRWXU)


def test_a_directory_that_cannot_even_be_created_refuses(tmp_path):
    parent = tmp_path / "ro"
    parent.mkdir()
    parent.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(StateDirError):
            assert_state_dirs_writable([parent / "child"])
    finally:
        parent.chmod(stat.S_IRWXU)
