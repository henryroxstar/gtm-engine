"""W2 boot check (PRD 2026-10-19a, test plan P2-1).

With ``GTM_TENANT_LAUNCHER=1`` a wrong workspaces root, a volume the backend's user does not own, or a
missing launcher binary refuses EVERY tenant run, late and one run at a time. The worker must instead refuse
to start, once, naming what is wrong. With the flag off there is no check, so a laptop or the cockpit path is
unchanged.

The real paths are fixed (``/app/data/workspaces``, uid 10001), so the checker takes them as arguments and
these tests point them at a temporary directory and the test process's own uid.
"""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import pytest

from backend import state_dirs, worker
from backend.state_dirs import LauncherBootError, assert_launcher_ready

ME = os.getuid()


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A workspaces root that is right, owned by 'the backend', with an executable launcher."""
    root = tmp_path / "workspaces"
    root.mkdir()
    launcher = tmp_path / "gtm-launch"
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("GTM_TENANT_LAUNCHER", "1")
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(root))
    monkeypatch.setenv("GTM_TENANT_LAUNCHER_PATH", str(launcher))
    return {"root": root.resolve(), "launcher": launcher}


def _check(world, **kw):
    kw.setdefault("launcher_root", world["root"])
    kw.setdefault("backend_uid", ME)
    return assert_launcher_ready(**kw)


def test_everything_right_passes(world):
    assert _check(world) is None


def test_wrong_workspaces_root_refuses_and_names_it(world, tmp_path, monkeypatch):
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(other))
    with pytest.raises(LauncherBootError) as e:
        _check(world)
    msg = str(e.value)
    assert "GTM_WORKSPACES_ROOT" in msg and str(other.resolve()) in msg
    assert str(world["root"]) in msg  # what it must be


def test_volume_not_owned_by_the_backend_user_refuses_and_names_the_owner(world):
    with pytest.raises(LauncherBootError) as e:
        _check(world, backend_uid=ME + 1)
    msg = str(e.value)
    assert "owned by" in msg and str(ME) in msg and str(ME + 1) in msg


def test_a_missing_root_refuses(world, monkeypatch, tmp_path):
    gone = tmp_path / "no-such-dir"
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(gone))
    with pytest.raises(LauncherBootError) as e:
        _check(world, launcher_root=gone.resolve())
    assert "does not exist" in str(e.value)


def test_a_missing_launcher_binary_refuses(world, monkeypatch, tmp_path):
    monkeypatch.setenv("GTM_TENANT_LAUNCHER_PATH", str(tmp_path / "absent"))
    with pytest.raises(LauncherBootError) as e:
        _check(world)
    assert "launcher" in str(e.value).lower()


def test_every_problem_is_reported_together_not_one_per_restart(world, monkeypatch, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(other))
    monkeypatch.setenv("GTM_TENANT_LAUNCHER_PATH", str(tmp_path / "absent"))
    with pytest.raises(LauncherBootError) as e:
        _check(world, backend_uid=ME + 1)
    msg = str(e.value)
    assert "GTM_WORKSPACES_ROOT" in msg and "owned by" in msg and "launcher" in msg.lower()


def test_the_fix_for_ownership_never_recurses(world):
    """A recursive chown would hand every pool user's tree back to the backend and break their isolation."""
    with pytest.raises(LauncherBootError) as e:
        _check(world, backend_uid=ME + 1)
    assert "chown -R" not in str(e.value)


@pytest.mark.parametrize("flag", [None, "", "0", "false", "no"])
def test_flag_off_means_no_check_at_all(world, monkeypatch, flag):
    if flag is None:
        monkeypatch.delenv("GTM_TENANT_LAUNCHER")
    else:
        monkeypatch.setenv("GTM_TENANT_LAUNCHER", flag)
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", "/definitely/not/the/root")
    monkeypatch.setenv("GTM_TENANT_LAUNCHER_PATH", "/definitely/not/a/launcher")
    assert _check(world, backend_uid=ME + 1) is None


def test_a_junk_flag_value_is_refused_not_read_as_off(world, monkeypatch):
    """The per-run path already treats 'yes' as a misconfiguration; booting as if the flag were off would
    quietly run every tenant unconfined while the operator believes the launcher is on."""
    monkeypatch.setenv("GTM_TENANT_LAUNCHER", "yes")
    with pytest.raises(Exception, match="GTM_TENANT_LAUNCHER"):
        _check(world)


def test_the_worker_refuses_before_it_touches_the_database(world, monkeypatch, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(other))
    monkeypatch.setenv("DATABASE_URL", "postgresql://never-used")

    async def no_pool(*a, **k):  # pragma: no cover - reaching it is the failure
        raise AssertionError("the worker connected to the database before the launcher check")

    monkeypatch.setattr(worker, "create_pool", no_pool)
    # The real constants are /app/data/workspaces and uid 10001, so on this machine the root is wrong.
    with pytest.raises(LauncherBootError):
        asyncio.run(worker.run_worker())


def test_the_real_constants_are_what_the_launcher_and_image_use():
    """Pin the two numbers the check hard-codes to the sources that define them."""
    repo = Path(state_dirs.__file__).parents[1]
    assert state_dirs.LAUNCHER_WORKSPACES_ROOT.as_posix() == "/app/data/workspaces"
    assert state_dirs.BACKEND_UID == 10001
    image = (repo / "deploy/Dockerfile.backend").read_text()  # the image the worker runs from
    assert "--uid 10001" in image
    assert "-DBACKEND_UID=10001" in image
    assert "-DWORKSPACES_PREFIX='\"/app/data/workspaces/\"'" in image
