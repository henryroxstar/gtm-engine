"""Per-profile exclusive prospect run lock with metadata and stale detection.

Prevents concurrent prospect runs against the same profile from corrupting
cumulative state (latest.json, ready-to-load.csv) or double-spending metered API credits.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover — Windows / non-POSIX
    fcntl = None  # type: ignore[assignment]

from gtm_core.prospect_paths import run_lock_path


@dataclass(frozen=True)
class RunLockInfo:
    """Metadata recorded while a prospect run holds the profile lock."""

    profile: str
    run_id: str
    pid: int
    started_at: str
    is_stale: bool = False


class RunLockBusy(RuntimeError):
    """Raised when a prospect run cannot acquire the profile lock due to contention."""

    def __init__(self, profile: str, run_id: str, holder: RunLockInfo | None = None) -> None:
        holder_desc = (
            f" (run_id={holder.run_id}, pid={holder.pid}, started={holder.started_at})"
            if holder
            else ""
        )
        super().__init__(
            f"Profile '{profile}' prospect run is locked by another process{holder_desc}"
        )
        self.profile = profile
        self.run_id = run_id
        self.holder = holder


def _meta_path(lock_file: Path) -> Path:
    return lock_file.with_name(f"{lock_file.name}.json")


def is_pid_alive(pid: int) -> bool:
    """Check whether a process with the given PID is currently alive."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError as err:
        return err.errno == errno.EPERM


def _read_meta(meta_file: Path) -> RunLockInfo | None:
    if not meta_file.exists():
        return None
    try:
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        pid = int(data.get("pid", -1))
        stale = not is_pid_alive(pid)
        return RunLockInfo(
            profile=str(data.get("profile", "")),
            run_id=str(data.get("run_id", "")),
            pid=pid,
            started_at=str(data.get("started_at", "")),
            is_stale=stale,
        )
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def get_lock_status(profile: str, content_root: Path | None = None) -> RunLockInfo | None:
    """Return the active RunLockInfo if currently locked, else None."""
    lock_file = run_lock_path(profile, content_root)
    meta_file = _meta_path(lock_file)

    if not lock_file.exists():
        if meta_file.exists():
            meta = _read_meta(meta_file)
            if meta and meta.is_stale:
                return meta
        return None

    try:
        fh = lock_file.open("r+")
    except (FileNotFoundError, OSError):
        return _read_meta(meta_file)

    active_meta: RunLockInfo | None = None
    try:
        if fcntl is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                meta = _read_meta(meta_file)
                if meta and meta.is_stale:
                    active_meta = meta
            except OSError as err:
                if err.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                    active_meta = _read_meta(meta_file) or RunLockInfo(
                        profile=profile,
                        run_id="unknown",
                        pid=-1,
                        started_at="unknown",
                        is_stale=False,
                    )
                else:
                    raise
        else:
            meta = _read_meta(meta_file)
            if meta and not meta.is_stale:
                active_meta = meta
    finally:
        fh.close()

    return active_meta


def break_stale_lock(profile: str, content_root: Path | None = None) -> bool:
    """Break a stale lock if the holding process is confirmed dead.

    Returns True if a stale lock was removed, False if no lock was present.
    Raises RunLockBusy if the lock is held by a live process.
    """
    lock_file = run_lock_path(profile, content_root)
    meta_file = _meta_path(lock_file)

    if not lock_file.exists() and not meta_file.exists():
        return False

    status = get_lock_status(profile, content_root)
    if status is not None and not status.is_stale:
        raise RunLockBusy(profile, "break", holder=status)

    # Clean up lock files
    try:
        if meta_file.exists():
            meta_file.unlink()
    except OSError:
        pass

    try:
        if lock_file.exists():
            if fcntl is not None:
                with lock_file.open("w") as fh:
                    try:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
            lock_file.unlink()
    except OSError:
        pass

    return True


@contextlib.contextmanager
def prospect_run_lock(
    profile: str,
    run_id: str,
    content_root: Path | None = None,
    *,
    blocking: bool = False,
) -> Iterator[RunLockInfo]:
    """Hold an exclusive prospect run lock for the profile.

    Args:
        profile: Active tenant profile.
        run_id: Unique identifier for this run.
        content_root: Optional override for content root.
        blocking: If True, wait until lock is available; if False, raise RunLockBusy immediately.

    Yields:
        RunLockInfo metadata for the acquired run lock.
    """
    lock_file = run_lock_path(profile, content_root)
    meta_file = _meta_path(lock_file)
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    fh = lock_file.open("w+")

    try:
        if fcntl is not None:
            flags = fcntl.LOCK_EX if blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                fcntl.flock(fh.fileno(), flags)
            except OSError as exc:
                if not blocking and exc.errno in (
                    errno.EACCES,
                    errno.EAGAIN,
                    errno.EWOULDBLOCK,
                ):
                    holder = _read_meta(meta_file)
                    raise RunLockBusy(profile, run_id, holder=holder) from None
                raise
        else:
            holder = _read_meta(meta_file)
            if holder and not holder.is_stale:
                if not blocking:
                    raise RunLockBusy(profile, run_id, holder=holder)

        info = RunLockInfo(
            profile=profile,
            run_id=run_id,
            pid=os.getpid(),
            started_at=datetime.now(UTC).isoformat(),
            is_stale=False,
        )

        try:
            meta_file.write_text(json.dumps(asdict(info), indent=2), encoding="utf-8")
        except OSError:
            pass

        yield info
    finally:
        try:
            if meta_file.exists():
                meta_file.unlink()
        except OSError:
            pass
        if fcntl is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        fh.close()


def main(argv: list[str] | None = None) -> int:
    """CLI for checking, breaking, and acquiring prospect run locks."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="gtm_core.run_lock",
        description="Inspect and manage profile prospect run locks.",
    )
    parser.add_argument("--profile", required=True, help="Tenant profile name")
    parser.add_argument(
        "action",
        choices=["status", "break"],
        help="Action to perform",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force break even if PID status is uncertain",
    )
    args = parser.parse_args(argv)

    if args.action == "status":
        info = get_lock_status(args.profile)
        if info is None:
            print(f"Profile '{args.profile}': unlocked")
            return 0
        state = "STALE" if info.is_stale else "LOCKED"
        print(
            f"Profile '{args.profile}': {state} by run_id={info.run_id} (pid={info.pid}, started={info.started_at})"
        )
        return 1 if not info.is_stale else 2

    if args.action == "break":
        try:
            broken = break_stale_lock(args.profile)
            if broken:
                print(f"Profile '{args.profile}': stale lock broken successfully")
            else:
                print(f"Profile '{args.profile}': no lock found")
            return 0
        except RunLockBusy as err:
            print(f"Cannot break active lock: {err}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
