"""Standalone queue worker process for GTM backend (A5 durable runs).

Spawns claim_loop and heartbeat_loop to process queued runs independently of the API HTTP server.
Can run in a separate Docker container so heavy agent executions do not contend with uvicorn.

Usage:
    python -m backend.worker
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

from .broker import connect as broker_connect
from .broker import require_broker, set_broker, worker_count
from .database import (
    advisory_singleton,
    assert_runtime_role_least_privilege,
    create_pool,
)
from .services.runs.events import on_broker_message, start_relay, stop_relay
from .services.runs.queue import claim_loop, heartbeat_loop, stop_task
from .session import BackendSessionStore
from .state_dirs import assert_launcher_ready

log = logging.getLogger("gtm_worker")
_RECONCILE_LOCK_KEY = 0x67746D5F72636E63  # "gtm_rcnc" — matches main.py


async def run_worker() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log.info("Starting GTM queue worker...")
    # W2: with the per-workspace launcher on, a wrong root/owner/launcher would refuse every tenant run, late.
    # Refuse to start instead, before any connection is opened.
    assert_launcher_ready()

    runtime_dsn = os.getenv("DATABASE_URL")
    if not runtime_dsn:
        raise RuntimeError("DATABASE_URL environment variable is required")

    pool = await create_pool(runtime_dsn, application_name="gtm-worker")
    await assert_runtime_role_least_privilege(pool)

    repo_root = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parents[1]))
    sessions = BackendSessionStore(repo_root)
    from .services.runs.state import register_session_store

    register_session_store(sessions)

    # Cost-reservation crash sweep (R4): RESERVATION_TTL_SECONDS MUST exceed the max run
    # wall time INCLUDING the 24h gate wait (GATE_TIMEOUT_S = 86400s), so default is 90000s (25h).
    reservation_ttl_s = int(os.getenv("RESERVATION_TTL_SECONDS", "90000"))

    async def _evict() -> None:
        while True:
            await asyncio.sleep(60)
            await sessions.close_idle()
            try:
                async with pool.acquire() as conn:
                    released = await conn.fetchval(
                        "SELECT release_stale_reservations($1)", reservation_ttl_s
                    )
                if released:
                    log.info("Released %s stale credit reservation(s)", released)
            except Exception:  # noqa: BLE001
                log.warning("Stale reservation sweep failed", exc_info=True)

    evict_task = asyncio.create_task(_evict())

    # Broker setup (cross-worker transport)
    broker = await broker_connect(on_broker_message)
    require_broker(worker_count(), broker)
    set_broker(broker)
    start_relay(pool)

    # Startup gate reconciliation
    from .routers.runs import reconcile_gates

    try:
        async with advisory_singleton(pool, _RECONCILE_LOCK_KEY):
            await reconcile_gates(pool, repo_root)
    except Exception:
        log.exception("Gate reconciliation failed at worker startup")

    queue_task = asyncio.create_task(claim_loop(pool, repo_root, sessions))
    heartbeat_task = asyncio.create_task(heartbeat_loop(pool))

    log.info("GTM queue worker running; waiting for runs.")

    stop_event = asyncio.Event()

    def _signal_handler():
        log.info("Received termination signal, shutting down worker...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    await stop_event.wait()

    # Shutdown sequence
    log.info("Stopping claim, heartbeat, and eviction tasks...")
    await stop_task(evict_task)
    await stop_task(queue_task)
    await stop_task(heartbeat_task)

    from .services.runs.state import drain_background_tasks

    log.info("Draining background tasks...")
    await drain_background_tasks()

    await stop_relay()
    if broker is not None:
        await broker.close()
        set_broker(None)
    await sessions.close_all()
    await pool.close()
    log.info("GTM queue worker cleanly stopped.")


def main():
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
