"""The prospect ledger's own write lock — held for one read-modify-write, never for a run.

``latest.json`` is rewritten whole by every writer (``upsert_latest``, ``set_status``,
``mutate_account``, ``restore``), so two of them interleaving is a lost update: the second
``os.replace`` lands a file computed from a read that predates the first. The write that gets
lost is the one that matters — an operator's ``do-not-contact`` landing between a discovery
run's read and its write simply disappears, and the atomic rename makes the loss look clean.

**Why this is not** :func:`gtm_core.locks.profile_lock`. That lock is held by the graph runner
for a whole pipeline run (``agent/pipeline.py``), and the prospect skill's CLIs run as child
processes *inside* that run. ``flock`` belongs to the open file description, so a child asking
for the lock its own parent holds waits forever. The ledger therefore has a lock of its own
(``prospects/.latest.lock``), taken only around the read-modify-write and released before the
call returns, so it can never be held across a run, a paid call, or a human gate.

**Re-entrant within a process, by path.** ``finalize`` → ``upsert_latest`` and
``mark_replied`` → ``set_status`` nest; a second ``flock`` on a fresh descriptor would block
on the first even in the same thread. The outermost entry on a path takes the ``flock``; nested
entries on the same thread only count. Other threads queue on a per-path ``RLock`` first.

Advisory, like every lock here: it serialises the writers that take it. A writer that does its
own read-modify-write of ``latest.json`` must wrap it in :func:`ledger_lock` too.

stdlib-only, to match the rest of gtm_core.
"""

from __future__ import annotations

import contextlib
import functools
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import IO, Any

LOCK_NAME = ".latest.lock"

_registry_guard = threading.Lock()
_thread_locks: dict[str, threading.RLock] = {}
_held: dict[str, tuple[int, IO[str] | None]] = {}  # lock path -> (depth, open handle)


def _flock(lock_path: Path) -> IO[str] | None:
    try:
        import fcntl  # POSIX-only; without it the in-process RLock is all there is.
    except ImportError:  # pragma: no cover — Windows
        return None
    handle = lock_path.open("a")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except BaseException:
        handle.close()
        raise
    return handle


@contextlib.contextmanager
def ledger_lock(ledger: Path, *, create: bool = False) -> Iterator[None]:
    """Hold the write lock for the ledger file ``ledger`` for the block's duration.

    ``create`` makes the ledger's folder if it is missing — right for a writer about to
    create the file. Without it a missing folder means there is no ledger to protect, and
    the block runs unlocked rather than inventing a tenant folder as a side effect of a
    read (a concurrent first write is then simply ordered after this call).
    """
    folder = ledger.parent
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    elif not folder.is_dir():
        yield
        return
    key = str((folder / LOCK_NAME).resolve())
    with _registry_guard:
        thread_lock = _thread_locks.setdefault(key, threading.RLock())
    with thread_lock:
        depth, handle = _held.get(key, (0, None))
        if depth == 0:
            handle = _flock(Path(key))
        _held[key] = (depth + 1, handle)
        try:
            yield
        finally:
            depth, handle = _held[key]
            if depth > 1:
                _held[key] = (depth - 1, handle)
            else:
                del _held[key]
                if handle is not None:
                    handle.close()  # closing the descriptor releases the flock


def serialised(
    path_of: Callable[[str, Path | None], Path], *, create: bool = False
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorate a ``fn(profile, ..., *, content_root=None)`` ledger writer so its whole
    read-modify-write runs under :func:`ledger_lock`."""

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(profile: str, *args: Any, content_root: Path | None = None, **kw: Any) -> Any:
            with ledger_lock(path_of(profile, content_root), create=create):
                return fn(profile, *args, content_root=content_root, **kw)

        return wrapper

    return decorate
