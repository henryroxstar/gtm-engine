"""Per-profile advisory file lock for the shared ``content/`` volume.

Two processes write the same profile's state tree: the cron ``hermes-brain`` (one-shot
pipeline runs) and the long-running ``hermes-cockpit`` (Telegram-driven runs). The
in-process ``asyncio.Lock`` in :class:`agent.session.SessionStore` only serialises within a
single process, so a cron radar run and a Telegram plan touching the same profile's run
manifest / plan files can interleave. This module adds a cross-process advisory lock
(``flock`` on ``content/<profile>/.lock``) so stage transitions for one profile are
serialised across both containers.

POSIX ``fcntl.flock`` is used (Linux VPS + macOS dev both support it). The lock is advisory:
it only blocks other holders of *this* lock, which is exactly the runner/cockpit cohort.
"""

from __future__ import annotations

import contextlib
import errno
from collections.abc import Iterator
from pathlib import Path


class LockBusy(RuntimeError):
    """Raised by :func:`profile_lock` in non-blocking mode when the lock is already held."""

    def __init__(self, profile: str) -> None:
        super().__init__(f"profile '{profile}' is locked by another process")
        self.profile = profile


@contextlib.contextmanager
def profile_lock(content_root: Path, profile: str, *, blocking: bool = True) -> Iterator[Path]:
    """Hold an exclusive advisory lock on ``content/<profile>/.lock`` for the block's duration.

    Args:
        content_root: the ``content/`` root (``Config.content_root`` or ``PathConfig.content_root``).
        profile: the tenant whose state is being mutated.
        blocking: if ``True`` (default), wait for the lock; if ``False``, raise
            :class:`LockBusy` immediately when another process holds it.

    Yields the lock file path. Always releases the lock and closes the handle on exit, even
    if the body raises.
    """
    import fcntl  # POSIX-only; imported lazily so the module imports on any platform.

    base = content_root / profile
    base.mkdir(parents=True, exist_ok=True)
    lock_path = base / ".lock"

    fh = lock_path.open("w")
    flags = fcntl.LOCK_EX if blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        try:
            fcntl.flock(fh.fileno(), flags)
        except OSError as exc:
            if not blocking and exc.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                raise LockBusy(profile) from None
            raise
        yield lock_path
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()
