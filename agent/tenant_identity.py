"""MM9 second layer: a tenant run's CLI starts as the workspace's OWN OS user, through ``gtm-launch``.

``agent/path_confinement.py`` reads command lines and refuses a path outside the run's tree. It cannot see
what a script the run wrote itself will open. Every workspace's run is a process in one container as one
user, so that script can read every other workspace's tree. The kernel can refuse it, if the run is a
different user from every other workspace and the trees are not readable across users.

This module is the Python half: a registry that gives each workspace one uid/gid from a fixed pool (recorded
once, never reused), and the plan that starts the CLI through the launcher as that identity. The launcher
(``deploy/launcher/gtm_launch.c``) is the only privileged piece; it holds ``CAP_SETUID``/``CAP_SETGID`` (to drop to
the workspace's user) and ``CAP_KILL`` (so the backend can still stop the run) and nothing else, accepts only an id
inside the pool, drops to it, sets ``no_new_privs`` and execs the bundled CLI. What the header of that file says it
refuses and why is the one place the launcher's behaviour is described; this module only builds its inputs.

Why one identity per WORKSPACE and not one per run (the design note's wording): the threat is tenant to
tenant. Two runs of the same workspace share one tree on purpose, so a per-run uid would add leasing and
cleanup and no boundary. A uid is never reused for another workspace, so nothing here needs to be released.

OFF by default (``GTM_TENANT_LAUNCHER``). With it on, a workspace tree is handed to its identity before its first
run starts (``ensure_provisioned``, called just before ``plan_for`` in ``agent/session.py``) through the launcher's
``--provision`` mode, which keeps the backend's own read and write through POSIX ACLs; a restored backup is
repaired with ``python -m agent.tenant_identity provision --all``. With the flag on and the launcher missing the
run is REFUSED, never started unconfined, and so is a tree the launcher refused to provision.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess  # nosec B404 — runs the launcher binary only, arg list, never shell=True
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .container_runner import ContainerLaunchPlan, prepare_container_launch  # noqa: F401

#: The pool the launcher accepts. Keep in step with ``UID_MIN``/``UID_MAX`` in ``gtm_launch.c`` (a test pins it).
POOL_MIN = 20000
POOL_MAX = 29999

ENABLED_ENV = "GTM_TENANT_LAUNCHER"
LAUNCHER_ENV = "GTM_TENANT_LAUNCHER_PATH"
DEFAULT_LAUNCHER = "/usr/local/sbin/gtm-launch"

_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_REGISTRY = "identities.json"


class IdentityPoolExhausted(RuntimeError):
    """Every identity in the pool is taken. A shared identity is no boundary, so this refuses."""


class RegistryCorrupt(RuntimeError):
    """The registry names an id outside the pool, or one id for two workspaces. Never repaired by guessing."""


class TreeNotProvisioned(RuntimeError):
    """The workspace tree is not owned by the identity the registry gives it, so the run is refused."""


class LauncherUnavailable(RuntimeError):
    """The launcher is switched on and not there. The run is refused rather than started unconfined."""


class ProvisionRefused(RuntimeError):
    """The launcher would not hand a tree to its identity. The run is refused; the tree's root stays unclaimed."""


LAUNCH_DIR = ".launch"
_STALE_AFTER_S = 300  # a private file is consumed within seconds of being written


class HandoffUnavailable(RuntimeError):
    """Something that must not reach the command line could not be written to a private file."""


@dataclass(frozen=True)
class LaunchPlan:
    cli_path: str
    env: dict[str, str]
    launch_dir: Path
    written: list = field(default_factory=list, compare=False)

    def discard(self) -> None:
        """Delete every private file this plan wrote and the launcher has not yet consumed (a failed build)."""
        for path in self.written:
            try:
                Path(path).unlink()
            except OSError:
                pass
        self.written.clear()

    def private_file(self, name: str, text: str) -> str:
        """Write ``text`` to a file only the backend's user can read, and return its path.

        The launcher accepts a secret-bearing flag (the MCP configuration, the system prompt) only as a path
        under ``<workspaces root>/.launch/``, reads the file while it is still the backend's user, deletes it and
        hands the contents to the run through an in-memory file. So a secret is never on a command line, which
        every user in the container can read.
        """
        import secrets
        import time

        self.launch_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions -- 0o700 is owner-only (the x bit lets the owner enter the directory); the rule flags any execute bit.
        os.chmod(self.launch_dir, 0o700)
        for stale in (
            self.launch_dir.iterdir()
        ):  # a run that never reached the launcher leaves its file behind
            try:
                if time.time() - stale.stat().st_mtime > _STALE_AFTER_S:
                    stale.unlink()
            except OSError:
                pass
        path = self.launch_dir / f"{secrets.token_hex(8)}-{name}"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as exc:
            raise HandoffUnavailable(f"cannot write the private launch file {name}: {exc}") from exc
        self.written.append(str(path))
        return str(path)


def identity_for(workspace_id: str, registry_dir: Path) -> int:
    """The workspace's uid (== gid), allocated once under a file lock and stable thereafter."""
    if not _NAME.fullmatch(workspace_id):
        raise ValueError(f"not a workspace id: {workspace_id!r}")
    registry_dir.mkdir(parents=True, exist_ok=True)
    with open(registry_dir / ".identities.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = registry_dir / _REGISTRY
        raw: dict[str, int] = json.loads(path.read_text()) if path.exists() else {}
        taken = {k: v for k, v in raw.items() if not k.startswith("_")}
        values = list(taken.values())
        if len(set(values)) != len(values) or any(
            not isinstance(v, int) or not POOL_MIN <= v <= POOL_MAX for v in values
        ):
            raise RegistryCorrupt(
                f"{path} names an id outside the pool or one id for two workspaces"
            )
        if workspace_id in taken:
            return taken[workspace_id]
        used = set(taken.values())
        if len(used) >= (POOL_MAX - POOL_MIN + 1):
            raise IdentityPoolExhausted(f"all {POOL_MAX - POOL_MIN + 1} identities are in use")
        seq = raw.get("_seq", POOL_MIN)
        free = next((i for i in range(seq, POOL_MAX + 1) if i not in used), None)
        if free is None:
            free = next((i for i in range(POOL_MIN, seq) if i not in used), None)
        if free is None:
            raise IdentityPoolExhausted(f"all {POOL_MAX - POOL_MIN + 1} identities are in use")
        raw[workspace_id] = free
        raw["_seq"] = (free + 1) if free < POOL_MAX else POOL_MIN
        tmp = path.with_suffix(".tmp")
        # 0600: a run can traverse the workspaces root to reach its own tree, and this file names every workspace
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            fh.write(json.dumps(raw, sort_keys=True))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(registry_dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)  # the books must survive what the trees survive
        finally:
            os.close(dir_fd)
        return free


def _bundled_cli() -> str:
    import claude_agent_sdk

    return str(Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude")


def _tree_owner(tree: Path) -> int | None:
    try:
        return tree.stat().st_uid
    except OSError:
        return None


def _launcher() -> str:
    launcher = os.environ.get(LAUNCHER_ENV) or DEFAULT_LAUNCHER
    if not (os.path.isfile(launcher) and os.access(launcher, os.X_OK)):
        raise LauncherUnavailable(f"{ENABLED_ENV}=1 but no executable launcher at {launcher}")
    return launcher


_PROVISION_TIMEOUT_S = 600


def provision(workspace_id: str, registry_dir: Path) -> int:
    """Hand ``<registry_dir>/<workspace_id>`` (the whole tree) to the workspace's identity; return the identity.

    The launcher does the privileged part and refuses anything but one canonical workspace tree; what it grants and
    why ACLs and not a group is its header's to say. Here: the run's HOME and TMPDIR exist before the hand-over (the
    plan points the CLI at them), the workspaces root stays traversable but not listable, and a refusal is raised.
    """
    ident = identity_for(workspace_id, registry_dir)
    launcher = _launcher()
    root = registry_dir.resolve()
    tree = root / workspace_id
    if not tree.is_dir() or tree.is_symlink():
        raise ProvisionRefused(f"workspace {workspace_id} has no tree under {root}")
    _make_run_dirs(tree, before_grant=True)
    os.chmod(root, 0o711)  # nosec B103  # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions -- traverse-only on purpose: runs reach their own tree, nobody can list the others
    os.chmod(root / _REGISTRY, 0o600)
    try:
        done = subprocess.run(  # nosec B603 — operator-pinned launcher path, arg list, never shell=True
            [launcher, "--provision", str(tree)],
            env={"GTM_RUN_UID": str(ident), "GTM_RUN_GID": str(ident)},
            capture_output=True,
            text=True,
            timeout=_PROVISION_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProvisionRefused(
            f"provisioning workspace {workspace_id} did not finish: {exc}"
        ) from exc
    if done.returncode != 0:
        why = (done.stderr.strip().splitlines() or [f"exit {done.returncode}"])[-1]
        raise ProvisionRefused(f"provisioning workspace {workspace_id} was refused: {why}")
    _make_run_dirs(tree, before_grant=False)
    return ident


def _make_run_dirs(tree: Path, *, before_grant: bool) -> None:
    """The run's HOME and TMPDIR. Made before the hand-over so the walk gives them to the identity; a tree restored
    closed to the backend only becomes writable once the launcher has granted it, so then they are made after."""
    for sub in ("home", "tmp"):
        try:
            (tree / ".run" / sub).mkdir(parents=True, exist_ok=True)
        except PermissionError:
            if not before_grant:
                raise


def _check_launcher_env() -> None:
    val = os.environ.get(ENABLED_ENV)
    if val is not None and val != "" and val.lower() not in ("1", "0", "false", "no"):
        raise LauncherUnavailable(
            "GTM_TENANT_LAUNCHER=1 enables the launcher; use GTM_TENANT_LAUNCHER_PATH to override the binary path"
        )


def release_identity(workspace_id: str, registry_dir: Path) -> bool:
    """Release a workspace's allocated UID/GID slot on account deletion (R10)."""
    if not _NAME.fullmatch(workspace_id):
        raise ValueError(f"not a workspace id: {workspace_id!r}")
    if not (registry_dir / _REGISTRY).exists():
        return False
    registry_dir.mkdir(parents=True, exist_ok=True)
    with open(registry_dir / ".identities.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = registry_dir / _REGISTRY
        if not path.exists():
            return False
        taken: dict[str, int] = json.loads(path.read_text())
        if workspace_id not in taken:
            return False
        del taken[workspace_id]
        tmp = path.with_suffix(".tmp")
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            fh.write(json.dumps(taken, sort_keys=True))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(registry_dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return True


def ensure_provisioned(workspace_id: str, registry_dir: Path) -> int | None:
    """With the launcher on, provision a tree whose root its identity does not own yet; else nothing."""
    _check_launcher_env()
    if os.environ.get(ENABLED_ENV) != "1":
        return None
    ident = identity_for(workspace_id, registry_dir)
    if _tree_owner(registry_dir / workspace_id) == ident:
        return ident
    return provision(workspace_id, registry_dir)


def main(argv: list[str] | None = None) -> int:
    """``provision (--all | <workspace id>...) [--root DIR]``: re-walk trees after a restore, whatever the flag."""
    import argparse

    from gtm_core.paths import resolve_workspaces_root

    parser = argparse.ArgumentParser(prog="python -m agent.tenant_identity")
    sub = parser.add_subparsers(dest="cmd", required=True)
    prov = sub.add_parser("provision", help="hand workspace trees to their identities (repair)")
    prov.add_argument("workspaces", nargs="*")
    prov.add_argument("--all", action="store_true", help="every workspace tree under the root")
    prov.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)
    root = args.root or resolve_workspaces_root()
    names = list(args.workspaces)
    if args.all:
        names += sorted(
            e.name
            for e in root.iterdir()
            if _NAME.fullmatch(e.name) and e.is_dir() and not e.is_symlink()
        )
    if not names:
        parser.error("name a workspace or pass --all")
    failed = 0
    for name in names:
        try:
            print(f"provisioned {name} as {provision(name, root)}")
        except (ValueError, RuntimeError, OSError) as exc:
            failed += 1
            print(f"refused {name}: {exc}", file=sys.stderr)
    return 1 if failed else 0


def plan_for(workspace_id: str, registry_dir: Path, *, run_dir: Path) -> LaunchPlan | None:
    """How to start this workspace's CLI, or ``None`` when the launcher is off."""
    _check_launcher_env()
    if os.environ.get(ENABLED_ENV) != "1":
        return None
    launcher = _launcher()
    cli = _bundled_cli()
    if not os.path.isfile(cli):
        raise LauncherUnavailable(f"{ENABLED_ENV}=1 but the SDK's bundled CLI is not at {cli}")
    ident = identity_for(workspace_id, registry_dir)
    if _tree_owner(run_dir.parent) != ident:
        # Fail closed on drift between the books and the disk: running as `ident` against a tree that is not its own
        # either cannot write it or, worse, matches another workspace's files after a registry restore.
        raise TreeNotProvisioned(
            f"workspace {workspace_id}'s tree is not owned by its identity {ident}; provision it before enabling {ENABLED_ENV}"
        )
    ident = str(ident)
    return LaunchPlan(
        cli_path=launcher,
        env={
            "GTM_RUN_UID": ident,
            "GTM_RUN_GID": ident,
            "GTM_REAL_CLI": cli,
            # No shared scratch: the CLI's config and every temp file live inside the run's own tree.
            "HOME": str(run_dir / "home"),
            "CLAUDE_CONFIG_DIR": str(run_dir / "home" / ".claude"),
            "TMPDIR": str(run_dir / "tmp"),
        },
        launch_dir=registry_dir / LAUNCH_DIR,
    )


if __name__ == "__main__":
    raise SystemExit(main())
