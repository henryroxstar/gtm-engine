"""cockpit.health — real liveness for the cockpit container (F7, always-on hardening PRD).

``import cockpit.bot`` proved the code *loads*; it said nothing about whether Telegram polling is alive. This
module makes liveness a fact:

* :func:`heartbeat_loop` — an asyncio task (the installed PTB ships no ``JobQueue``) that touches
  ``content/_system/cockpit-heartbeat`` **only while ``application.updater.running``**. A blocked event loop
  cannot touch it, and neither can a bot whose polling has stopped.
* :class:`Watchdog` — a thread, independent of the event loop, that exits the process non-zero when the
  heartbeat is stale. Docker's ``restart: unless-stopped`` restarts an *exited* container (not an
  *unhealthy* one), so exiting is the whole mechanism — no autoheal container, no Docker socket (PRD D3).
  It will **not** exit while a publish/enroll dispatch is in flight (:mod:`agent.inflight`), up to a hard cap,
  so it can never manufacture an "outcome unknown".
* ``python -m cockpit.health`` — the compose health check: exit 0 iff the heartbeat is fresh.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from agent import inflight

logger = logging.getLogger("cockpit.bot")

HEARTBEAT_NAME = "cockpit-heartbeat"
DEFAULT_MAX_AGE_S = 120
EXIT_CODE = 70  # EX_SOFTWARE


def heartbeat_path(content_root: Path) -> Path:
    return Path(content_root) / "_system" / HEARTBEAT_NAME


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def age_s(path: Path, wall: Callable[[], float] = time.time) -> float | None:
    """Seconds since the heartbeat was last touched, or None when it does not exist."""
    try:
        return wall() - path.stat().st_mtime
    except OSError:
        return None


async def heartbeat_loop(application, path: Path, interval_s: float = 15.0) -> None:
    """Touch ``path`` every ``interval_s`` while — and only while — polling is running."""
    while True:
        try:
            if getattr(application.updater, "running", False):
                touch(path)
        except OSError:
            logger.debug("could not touch the heartbeat", exc_info=True)
        await asyncio.sleep(interval_s)


class Watchdog:
    def __init__(
        self,
        path: Path,
        *,
        limit_s: float,
        grace_s: float = 60.0,
        inflight_cap_s: float = 600.0,
        exit_fn: Callable[[int], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        poll_s: float = 15.0,
    ) -> None:
        self._path = path
        self._limit = limit_s
        self._grace = grace_s
        self._cap = inflight_cap_s
        self._exit = exit_fn or os._exit
        self._clock = clock
        self._wall = wall
        self._poll = poll_s
        self._started = clock()
        self._stale_since: float | None = None
        self._stop = threading.Event()

    def check_once(self) -> bool:
        """One watchdog decision. True iff it asked the process to exit."""
        if self._limit <= 0:
            return False
        age = age_s(self._path, self._wall)
        if age is None:
            stale = (
                self._clock() - self._started > self._grace
            )  # a missing file is boot until grace ends
        else:
            stale = age > self._limit
        if not stale:
            self._stale_since = None
            return False
        now = self._clock()
        if self._stale_since is None:
            self._stale_since = now
        if inflight.count() > 0 and now - self._stale_since <= self._cap:
            return False  # a send is mid-flight: restarting now would leave its outcome unknown
        logger.critical(
            "heartbeat stale (%s s) — watchdog exiting so Docker restarts the cockpit", age
        )
        self._exit(EXIT_CODE)
        return True

    def start(self) -> threading.Thread:
        def run() -> None:
            while not self._stop.wait(self._poll):
                if self.check_once():
                    return

        t = threading.Thread(target=run, name="cockpit-watchdog", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()


def main() -> int:
    """``python -m cockpit.health`` — the compose HEALTHCHECK. 0 = heartbeat fresh, 1 = stale/missing."""
    from gtm_core.paths import resolve_content_root

    try:
        max_age = float(os.getenv("COCKPIT_HEALTH_MAX_AGE_S") or DEFAULT_MAX_AGE_S)
    except ValueError:
        max_age = DEFAULT_MAX_AGE_S
    age = age_s(heartbeat_path(resolve_content_root()))
    return 0 if age is not None and age <= max_age else 1


if __name__ == "__main__":
    sys.exit(main())
