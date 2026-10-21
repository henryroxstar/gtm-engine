"""agent.inflight — a process-wide counter of outbound dispatches that are mid-flight (F7/F4).

The cockpit watchdog restarts a hung bot. It must never do that *between* an outbound call's intent and its
outcome — that is how a watchdog would manufacture an "outcome unknown". Every publish / enroll dispatch
therefore runs inside :func:`track`; the watchdog (``cockpit.health``) refuses to exit while the count is
non-zero (up to a hard cap), and a ``SIGTERM`` waits for it via :func:`wait_idle`.

Thread-safe and exception-safe; carries no data, so it cannot leak anything.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

_lock = threading.Lock()
_count = 0
_by_kind: dict[str, int] = {}


def count() -> int:
    with _lock:
        return _count


def kinds() -> set[str]:
    """The kinds of dispatch in flight right now (``email``, ``post``, …) — what ``/stop`` tells the operator."""
    with _lock:
        return {k for k, n in _by_kind.items() if n > 0}


@contextmanager
def track(kind: str = "send") -> Iterator[None]:
    """Mark one outbound dispatch of ``kind`` as in flight for the duration of the ``with`` block."""
    global _count
    with _lock:
        _count += 1
        _by_kind[kind] = _by_kind.get(kind, 0) + 1
    try:
        yield
    finally:
        with _lock:
            _count -= 1
            _by_kind[kind] -= 1


def wait_idle(timeout_s: float, poll_s: float = 0.05) -> bool:
    """Block until no dispatch is in flight; False if ``timeout_s`` elapsed first."""
    deadline = time.monotonic() + timeout_s
    while True:
        if count() == 0:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_s)
