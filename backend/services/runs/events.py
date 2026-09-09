"""SSE plumbing: frame formatting, the per-run publish/subscribe fed at every
status-write site of the executors, and the durable publish relay (A5 step 3).

Two publish entry points, and the distinction is the whole design:

* :func:`publish_run_event` is what a run calls. It takes an explicit
  ``workspace_id``, so the event is written to the RLS-scoped ``run_events`` table
  (giving it a durable, replayable id) and fanned out to every worker through the
  broker. This is the only publish a production code path may use.
* :func:`_publish_event` is the LOCAL transport underneath it — deliver to this
  process's subscribers, never raise, drop-oldest-then-poison on overflow. It is what
  ``publish_run_event`` ultimately calls, and it is unchanged from protocol 1.

Why ``publish_run_event`` takes the workspace explicitly rather than looking it up:
``run_events`` is under FORCE ROW LEVEL SECURITY, so a row cannot be written without a
tenant binding, and every one of the call sites already has ``workspace_id`` in scope.
Inferring it later from a run-id map would add exactly the kind of ambient process state
this work exists to delete — and a map that can miss is a row that silently is not
written.

The relay exists because ``publish_run_event`` must stay SYNC and never block a run:
making it async — to await an INSERT and a PUBLISH — would push ``await`` into all 14
call sites across the executors. Instead it hands the event to a single-consumer
``asyncio.Queue`` drained by one relay task per worker, which assigns the durable id and
then fans out. One consumer preserves per-worker ordering.

With NO relay running (a unit test, any process that never ran the lifespan) publishing
falls back to immediate local fan-out with no durable id — i.e. exactly protocol-1
behaviour. That is a real degradation path, not an accident: the local transport was
always complete on its own, and durability is what the relay adds.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from ...broker import GATE_CHANNEL_PREFIX, RUN_CHANNEL_PREFIX, WORKER_ID, get_broker
from ...database import workspace_scope
from .state import _STREAM_QUEUE_MAX, _gate_events, _run_subscribers

log = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sse_frame(event: str, data: dict, *, seq: int | None = None) -> str:
    """Format one text/event-stream frame (id/event/data lines, blank-line terminated)."""
    parts = []
    if seq is not None:
        parts.append(f"id: {seq}")
    parts.append(f"event: {event}")
    parts.append(f"data: {json.dumps(data, separators=(',', ':'))}")
    return "\n".join(parts) + "\n\n"


class Frame(tuple):
    """The subscriber-queue payload: a plain ``(event, data)`` 2-tuple that also carries
    the event's durable ``run_events.id``.

    A 2-tuple rather than a 3-tuple on purpose — ``event, data = await q.get()`` is the
    reader contract protocol 0 and 1 were written against, and the overflow poison is
    compared as a 2-tuple. The seq rides alongside as an attribute, so protocol 2 adds
    the id without changing the shape anything already reads. (No ``__slots__``: a
    variable-length built-in subtype cannot have one.)
    """

    def __new__(cls, event: str, data: dict, seq: int | None = None):
        obj = super().__new__(cls, (event, data))
        obj.seq = seq
        return obj


# Poison marker enqueued on overflow: the generator closes the stream so the
# client reconnects. Under protocol 2 that reconnect can REPLAY from ?since=<last id>
# rather than re-snapshotting, so backpressure no longer costs the client its event
# history — but the close itself is unchanged, because silently dropping events could
# absorb an awaiting_approval, which must never be possible.
_OVERFLOW_CLOSE = ("__overflow_close__", {})


def _publish_event(run_id: str, event: str, data: dict, *, seq: int | None = None) -> None:
    """Push an event to every SSE subscriber of run_id IN THIS PROCESS. Never raises.

    The local transport only — no durability, no cross-worker fan-out. Production code
    calls :func:`publish_run_event` instead; this is what that function delivers
    through, and what a client of a single process has always received.

    Bounded queue; on overflow the stream is CLOSED (drop-oldest-then-poison) — a
    slow/dead consumer can never stall a run, and a client that fell behind resyncs on
    reconnect rather than silently missing frames.
    """
    subs = _run_subscribers.get(run_id)
    if not subs:
        return
    payload = Frame(event, data, seq)
    for q in subs:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                q.get_nowait()  # make room, then poison — the generator closes
                q.put_nowait(_OVERFLOW_CLOSE)
            except Exception:  # noqa: BLE001
                pass  # nosec B110 — intentional best-effort swallow
        except Exception:  # noqa: BLE001
            pass  # nosec B110 — intentional best-effort swallow


def _subscribe(run_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=_STREAM_QUEUE_MAX)
    _run_subscribers.setdefault(run_id, set()).add(q)
    return q


def _unsubscribe(run_id: str, q: asyncio.Queue) -> None:
    subs = _run_subscribers.get(run_id)
    if subs is None:
        return
    subs.discard(q)
    if not subs:
        _run_subscribers.pop(run_id, None)


# ── the durable publish relay (A5 step 3) ─────────────────────────────────────

#: Bounded: a relay that fell arbitrarily behind would hold the whole event history of
#: a burst in memory. Over the bound, publishing degrades to immediate local fan-out
#: (an event is never dropped, it just loses its durable id).
_RELAY_QUEUE_MAX = 2048

_relay_queue: asyncio.Queue | None = None
_relay_task: asyncio.Task | None = None


def publish_run_event(workspace_id: str, run_id: str, event: str, data: dict) -> None:
    """Publish one run event: durable, cross-worker, and local. Sync; never raises.

    ``workspace_id`` is required — see the module docstring. With a relay running the
    event is queued for its ``run_events`` INSERT (which assigns the seq clients replay
    against) and its broker publish; with no relay, or a saturated one, it is delivered
    locally right here, which is protocol-1 behaviour exactly.
    """
    q = _relay_queue
    if q is None:
        _publish_event(run_id, event, data)
        return
    try:
        q.put_nowait((workspace_id, run_id, event, data))
    except asyncio.QueueFull:
        log.warning("publish relay saturated; delivering %s locally without a seq", event)
        _publish_event(run_id, event, data)


async def _relay_one(pool, workspace_id: str, run_id: str, event: str, data: dict) -> None:
    """Persist one event, then fan it out locally and to the other workers."""
    seq: int | None = None
    try:
        async with workspace_scope(pool, workspace_id) as conn:
            seq = await conn.fetchval(
                """INSERT INTO run_events(run_id, workspace_id, event, data)
                   VALUES($1::uuid, $2::uuid, $3, $4::jsonb) RETURNING id""",
                run_id,
                workspace_id,
                event,
                json.dumps(data, ensure_ascii=False),
            )
    except Exception:  # noqa: BLE001 — a failed durable write must not lose the frame
        log.warning("run_events insert failed for %s/%s", run_id, event, exc_info=True)

    _publish_event(run_id, event, data, seq=seq)

    broker = get_broker()
    if broker is not None:
        await broker.publish(
            f"{RUN_CHANNEL_PREFIX}{run_id}",
            {"worker": WORKER_ID, "seq": seq, "event": event, "data": data},
        )


async def _relay_loop(pool) -> None:
    assert _relay_queue is not None
    while True:
        workspace_id, run_id, event, data = await _relay_queue.get()
        try:
            await _relay_one(pool, workspace_id, run_id, event, data)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — one bad event must never stop the relay
            log.warning("publish relay failed on %s/%s", run_id, event, exc_info=True)
        finally:
            _relay_queue.task_done()


def start_relay(pool) -> asyncio.Task:
    """Start this worker's single relay consumer. Idempotent."""
    global _relay_queue, _relay_task  # noqa: PLW0603 — one relay per process, by design
    if _relay_task is not None and not _relay_task.done():
        return _relay_task
    _relay_queue = asyncio.Queue(maxsize=_RELAY_QUEUE_MAX)
    _relay_task = asyncio.create_task(_relay_loop(pool))
    return _relay_task


async def stop_relay() -> None:
    """Drain what is already queued, then stop. Called from the lifespan shutdown."""
    global _relay_queue, _relay_task  # noqa: PLW0603 — one relay per process, by design
    task, queue = _relay_task, _relay_queue
    _relay_task, _relay_queue = None, None
    if task is None:
        return
    if queue is not None:
        try:
            await asyncio.wait_for(queue.join(), timeout=5.0)
        except (TimeoutError, Exception):  # noqa: BLE001 — shutdown is best-effort
            pass  # nosec B110
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass  # nosec B110 — shutdown path


def on_broker_message(channel: str, payload: dict) -> None:
    """Deliver another worker's message into this process (broker listener callback).

    A run frame joins the local subscriber queues carrying the ORIGINATING worker's
    durable seq, so every client sees the same ids for the same events regardless of
    which worker it is streaming from. A gate message is a bare wake: it sets the local
    ``asyncio.Event`` and nothing else — the waiting run reads the decision from the
    durable ``run_gates`` row, which is the only place a gate decision is ever read
    from.
    """
    if channel.startswith(RUN_CHANNEL_PREFIX):
        run_id = channel[len(RUN_CHANNEL_PREFIX) :]
        event = payload.get("event")
        data = payload.get("data")
        if isinstance(event, str) and isinstance(data, dict):
            _publish_event(run_id, event, data, seq=payload.get("seq"))
        return
    if channel.startswith(GATE_CHANNEL_PREFIX):
        run_id = channel[len(GATE_CHANNEL_PREFIX) :]
        waiter = _gate_events.get(run_id)
        if waiter is not None:
            waiter.set()
