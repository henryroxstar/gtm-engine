"""Refuse to boot when the backend cannot write the state it owns (MM10).

The image runs as an unprivileged user. A named volume that an earlier root-owned deploy created is
still owned by root, so the first write to it would fail minutes later inside a run, as an opaque
``PermissionError`` in someone's workspace. Failing at boot, once, with the command that fixes it, is
the only version of that failure a person can act on.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path


class StateDirError(RuntimeError):
    """A directory the backend must write is not writable by its user."""


class LauncherBootError(StateDirError):
    """The per-workspace launcher is switched on and the box is not set up for it (W2)."""


#: The launcher accepts private files only under, and provisions only directly below, this path, and the image's
#: backend user is this uid. Both are hard-coded in the launcher/Dockerfile; a test pins them to those sources.
LAUNCHER_WORKSPACES_ROOT = Path("/app/data/workspaces")
BACKEND_UID = 10001


def assert_launcher_ready(
    *, launcher_root: Path = LAUNCHER_WORKSPACES_ROOT, backend_uid: int = BACKEND_UID
) -> None:
    """With ``GTM_TENANT_LAUNCHER=1``, refuse to boot unless every tenant run could be launched.

    Three things refuse EVERY run when wrong, one run at a time and late: the workspaces root (the launcher
    works only under ``/app/data/workspaces``), the owner of that root (provisioning sets it to 0711 and needs
    the backend's own uid to own it), and the launcher binary. All problems are reported together. With the
    flag off nothing is checked, so the cockpit and a laptop are unchanged. A junk flag value raises the same
    error the per-run path raises, rather than booting as if the launcher were off.
    """
    from agent import tenant_identity as ti
    from gtm_core.paths import resolve_workspaces_root

    ti._check_launcher_env()
    if os.environ.get(ti.ENABLED_ENV) != "1":
        return
    problems: list[str] = []
    root = resolve_workspaces_root()
    if root != launcher_root.resolve():
        problems.append(
            f"GTM_WORKSPACES_ROOT resolves to {root}, but the launcher works only under {launcher_root} "
            f"(set GTM_WORKSPACES_ROOT={launcher_root})"
        )
    # The owner that matters is the required root's, whatever GTM_WORKSPACES_ROOT says: that is where the
    # launcher will look. Checked independently so both problems come out in one boot, not one per restart.
    try:
        owner = launcher_root.stat().st_uid
    except OSError:
        problems.append(
            f"the workspaces root {launcher_root} does not exist (mount the `workspaces` volume there)"
        )
    else:
        if owner != backend_uid:
            problems.append(
                f"the workspaces root {launcher_root} is owned by uid {owner}, not the backend's uid {backend_uid}. "
                f"Fix it once, NOT recursively (a recursive chown takes every pool user's tree back): "
                f"`docker run --rm -v <the workspaces volume>:/v alpine chown {backend_uid}:{backend_uid} /v`"
            )
    try:
        ti._launcher()
    except ti.LauncherUnavailable as exc:
        problems.append(f"the launcher is missing: {exc}")
    if problems:
        raise LauncherBootError(
            "GTM_TENANT_LAUNCHER=1 but this box cannot launch tenant runs: " + "; ".join(problems)
        )


def assert_state_dirs_writable(paths: Iterable[Path]) -> None:
    """Create each directory if absent and require this process to be able to write into it."""
    uid = os.getuid() if hasattr(os, "getuid") else None
    for path in paths:
        try:
            path.mkdir(parents=True, exist_ok=True)
            writable = os.access(path, os.W_OK | os.X_OK)
        except OSError:
            writable = False
        if not writable:
            raise StateDirError(
                f"{path} is not writable by the backend's user (uid {uid}). The image runs "
                f"unprivileged, so a volume created by an earlier root-owned deploy must be chowned "
                f"once: `docker run --rm -v <the volume>:/v alpine chown -R 10001:10001 /v`."
            )
