"""The ONE Redis surface in the codebase (A5 step 3) — cross-worker fan-out only.

Two channels, one namespace:

===========================  ========================================  ==========================
channel                      payload                                   who publishes → who reads
===========================  ========================================  ==========================
``gtm:run:{run_id}``         ``{worker, seq, event, data}``            any worker → every worker
                                                                       serving that run's stream
``gtm:run:{run_id}``         ``{worker, resync: true}`` — an event     the worker that lost it →
                             got no durable id (ST-16)                 every worker, which sends
                                                                       its streams a snapshot
``gtm:gate:{run_id}``        ``{worker, run_id}`` — a bare WAKE        the worker handling
                                                                       POST /gate → the worker
                                                                       holding the run
===========================  ========================================  ==========================

**The gate channel carries a wake, not a decision.** The decision is read from the
durable ``run_gates`` row by the waiting worker, which already treats that row as
authoritative. A decision travelling over an unauthenticated cache would be a second,
weaker path to resolving a human gate; there must not be one.

**Correctness never depends on this module.** Postgres is the source of truth for the
queue, the gate decision, and the event log; Redis is a latency optimisation with a poll
backstop behind it (``hold_gate`` re-reads the row every ``GATE_POLL_S``). Redis is
cache-only in compose (``--save "" --appendonly no``) and must not be trusted to persist
anything.

**No new egress path (§R6).** ``redis`` is a compose-internal service on the ``backend``
bridge network, ``expose``d and never published to a host port, reached by service name.
It is not an outbound destination and adds no allowlist entry.

**No second config path (§2.3 rule 11).** The URL resolves through the *existing*
rate-limit precedence (``RATELIMIT_STORAGE_URI`` → ``REDIS_URL`` → none), so the storage
the limiter shares and the storage the fan-out uses can never drift apart.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import uuid
from collections.abc import Callable

log = logging.getLogger(__name__)

#: This process's identity, used for BOTH halves of A5: the queue's ``claimed_by``
#: (who owns this run) and the publish origin (so a worker ignores its own messages
#: coming back around the fan-out). The boot-uuid segment matters — a container that
#: restarts and reuses a pid must not look like the previous owner, or the lease
#: reclaimer would treat a dead worker's runs as live.
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"

RUN_CHANNEL_PREFIX = "gtm:run:"
GATE_CHANNEL_PREFIX = "gtm:gate:"
_PATTERNS = (f"{RUN_CHANNEL_PREFIX}*", f"{GATE_CHANNEL_PREFIX}*")


def broker_url() -> str | None:
    """The broker URI, or None when there is no shared broker configured.

    Deliberately delegates to the rate limiter's resolver rather than reading its own
    env var: one storage decision, one precedence, no way for the limiter and the SSE
    fan-out to end up pointed at different Redis instances.
    """
    from .ratelimit import _resolve_storage_uri

    uri = _resolve_storage_uri()
    return None if uri == "memory://" else uri


class Broker:
    """A connected Redis pub/sub client plus this worker's single listener task.

    ONE pattern subscription per worker (not one per stream): remote messages are
    injected into the existing in-process subscriber registry, so every consumer keeps
    reading from the same local queue whether the event was produced here or elsewhere.
    """

    def __init__(self, client, handler: Callable[[str, dict], None]) -> None:
        self._client = client
        self._handler = handler
        self._pubsub = None
        self._task: asyncio.Task | None = None

    async def publish(self, channel: str, payload: dict) -> None:
        """Fire one message. Never raises: a broker hiccup must not fail a run — the
        durable row was already written and the poll backstop still resolves it."""
        try:
            await self._client.publish(channel, json.dumps(payload, separators=(",", ":")))
        except Exception:  # noqa: BLE001
            log.warning("broker publish to %s failed", channel, exc_info=True)

    async def start(self) -> None:
        self._pubsub = self._client.pubsub(ignore_subscribe_messages=True)
        await self._pubsub.psubscribe(*_PATTERNS)
        self._task = asyncio.create_task(self._listen())

    async def _listen(self) -> None:
        assert self._pubsub is not None
        while True:
            try:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=5.0
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a dropped connection must not kill the task
                log.warning("broker listener read failed; retrying", exc_info=True)
                await asyncio.sleep(1.0)
                continue
            if message is None:
                continue
            try:
                channel = message["channel"]
                if isinstance(channel, bytes):
                    channel = channel.decode()
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode()
                payload = json.loads(data)
            except Exception:  # noqa: BLE001 — malformed message is data, not a crash
                log.warning("broker listener: undecodable message", exc_info=True)
                continue
            if not isinstance(payload, dict):
                continue
            # A worker's own publishes were already delivered locally at publish time;
            # re-injecting them here would double every frame.
            if payload.get("worker") == WORKER_ID:
                continue
            try:
                self._handler(channel, payload)
            except Exception:  # noqa: BLE001
                log.warning("broker handler raised on %s", channel, exc_info=True)

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass  # nosec B110 — shutdown path, nothing to recover
        if self._pubsub is not None:
            try:
                await self._pubsub.close()
            except Exception:  # noqa: BLE001
                pass  # nosec B110 — shutdown path
        try:
            await self._client.aclose()
        except Exception:  # noqa: BLE001
            pass  # nosec B110 — shutdown path


# Module-level singleton: one broker per process, set by the lifespan. A global rather
# than app.state because the publish side is reached from the run services, which have
# no request or app handle (and must not grow one).
_broker: Broker | None = None


def get_broker() -> Broker | None:
    return _broker


def set_broker(broker: Broker | None) -> None:
    global _broker  # noqa: PLW0603 — the one process-wide handle, by design
    _broker = broker


async def connect(handler: Callable[[str, dict], None]) -> Broker | None:
    """Connect + start the listener, or return None when no broker is configured.

    Returns None (never raises) on an absent ``redis`` package or an unreachable
    server: at one worker the in-process transport is complete and correct, so this
    degrades exactly as the rate limiter does. ``require_broker`` is what makes the
    multi-worker case fail closed instead.
    """
    uri = broker_url()
    if uri is None:
        return None
    try:
        import redis.asyncio as redis_asyncio
    except ImportError:
        log.warning("REDIS_URL/RATELIMIT_STORAGE_URI is set but the redis package is missing")
        return None
    try:
        client = redis_asyncio.from_url(uri)
        await client.ping()
        broker = Broker(client, handler)
        await broker.start()
    except Exception:  # noqa: BLE001 — unreachable broker degrades, it does not crash boot
        log.warning("broker at %s unreachable; cross-worker fan-out is off", uri, exc_info=True)
        return None
    log.info("broker connected (%s)", uri)
    return broker


def require_broker(workers: int, broker: Broker | None) -> None:
    """Fail-closed boot guard: >1 worker with no reachable broker refuses to start.

    At one worker the existing graceful degradation is unchanged, so dev, the test
    suite, and today's deployed single-worker stack all keep working with no Redis at
    all. A multi-worker process that silently fell back to per-process rate limiters,
    a split SSE fan-out, and gates that only wake on the 15s poll is precisely the
    green-but-wrong failure this guard exists to prevent — it would stay invisible
    until a customer lost a run.
    """
    if workers > 1 and broker is None:
        raise RuntimeError(
            f"BACKEND_WORKERS={workers} requires a reachable shared broker, but none is "
            "configured or connectable. Set RATELIMIT_STORAGE_URI (or REDIS_URL) to a "
            "redis:// URI the container can reach, or run a single worker. Refusing to "
            "boot rather than run multi-worker on per-process state."
        )


def worker_count() -> int:
    """``BACKEND_WORKERS``, defaulting to 1. A value < 1 reads as 1."""
    try:
        return max(1, int(os.getenv("BACKEND_WORKERS", "1")))
    except ValueError:
        return 1


async def publish_gate_wake(run_id: str) -> None:
    """Wake whichever worker holds this run's gate. A bare wake — never the decision."""
    broker = get_broker()
    if broker is None:
        return
    await broker.publish(f"{GATE_CHANNEL_PREFIX}{run_id}", {"worker": WORKER_ID, "run_id": run_id})
