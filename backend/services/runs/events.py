"""SSE plumbing: frame formatting and the per-run publish/subscribe fed at every
status-write site of the executors. Bounded queues, drop-oldest-then-poison on overflow so
a slow consumer can never stall a run. Transport-agnostic behind these helpers (a
multi-worker deploy swaps the dict for LISTEN/NOTIFY or a broker without touching the
executors)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from .state import _STREAM_QUEUE_MAX, _run_subscribers


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


# Poison marker enqueued on overflow: the generator closes the stream so the
# client reconnects and resyncs from a FRESH snapshot. Interim A2 backpressure
# contract — silently dropping events could absorb an awaiting_approval, which
# must never be possible; real per-run seq + replay arrives with A5's DB events.
_OVERFLOW_CLOSE = ("__overflow_close__", {})


def _publish_event(run_id: str, event: str, data: dict) -> None:
    """Push an event to every SSE subscriber of run_id. Never raises.

    Bounded queue; on overflow the stream is CLOSED (drop-oldest-then-poison) —
    a slow/dead consumer can never stall _execute_run, and a client that fell
    behind rebuilds from the reconnect snapshot instead of silently missing
    frames.
    """
    subs = _run_subscribers.get(run_id)
    if not subs:
        return
    payload = (event, data)
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
