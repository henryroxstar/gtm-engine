"""The run event stream (`GET /v1/runs/{id}/stream`) — the SSE generator itself.

Split out of the router in Phase 1b: what stays there is the HTTP shell (the per-workspace
stream cap, the 404, the ``StreamingResponse``), and what lives here is the protocol —
subscribe-before-snapshot, the snapshot frame, heartbeats, backpressure close, and the
accounting that must run in a ``finally`` so a dropped client always frees its slot.

**Protocol 2 (A5 step 3).** ``seq`` is now the DURABLE ``run_events.id`` rather than a
per-connection counter, which is what makes resume possible: the snapshot advertises the
watermark it was taken at, and a client can reconnect with ``?since=<id>`` to be handed
exactly the events after that id instead of a fresh rebuild.

**Why ``?since=`` and not the ``Last-Event-ID`` header.** Protocol 1 defines ``seq`` as
per-connection and tells clients to reset it on reconnect, so a protocol-1 client's last
seq is a small number like ``7`` — indistinguishable on the wire from a protocol-2
durable id. Honouring the header would therefore mean interpreting a protocol-1 client's
value under protocol-2 rules (replaying from ``run_events.id > 7``: garbage), and any
fetch-SSE transport that echoes the header automatically would silently opt a client into
a response shape it never asked for and does not expect — no snapshot. An explicit query
parameter cannot be sent by accident, so resume is strictly opt-in and protocol 1 is
untouched. ``Last-Event-ID`` stays ignored, exactly as protocol 1 documented.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable

from ...database import workspace_scope
from .events import _OVERFLOW_CLOSE, _sse_frame, _subscribe, _unsubscribe, _utc_now
from .state import _STREAM_HEARTBEAT_S, _TERMINAL_STATUSES, _state_lock, _workspace_stream_count

#: The stream vocabulary this server emits (snapshot.protocol).
PROTOCOL = 2

#: Past this many missed events a resume is abandoned for a fresh snapshot: a client that
#: far behind is cheaper to rebuild than to catch up, and replaying an unbounded backlog
#: into a bounded queue would just trip the overflow close anyway.
REPLAY_MAX = 1000


class _Opening:
    """What one transaction reads to build the stream's opening frames.

    ``replay`` is None when the client did not resume (or fell too far behind), in which
    case ``nodes``/``blocks`` were read for a snapshot instead. Reading all of it in ONE
    transaction is what makes ``head`` exact: "everything after this id" is then precisely
    what the snapshot does not already contain — no gap, no double delivery.
    """

    __slots__ = ("blocks", "head", "nodes", "replay", "row")

    def __init__(self, row, head: int, replay, nodes, blocks) -> None:
        self.row = row
        self.head = head
        self.replay = replay
        self.nodes = nodes
        self.blocks = blocks


async def _read_opening(conn, workspace_id: str, run_id: str, since: int | None) -> _Opening | None:
    """The single opening read. None when the run vanished between the 404 check and here."""
    row = await conn.fetchrow(
        """SELECT id::text, status, output, error, error_code, pending_gate, pending_content
           FROM runs WHERE id = $1::uuid AND workspace_id = $2::uuid""",
        run_id,
        workspace_id,
    )
    if row is None:
        return None
    head = await conn.fetchval(
        """SELECT coalesce(max(id), 0) FROM run_events
           WHERE run_id = $1::uuid AND workspace_id = $2::uuid""",
        run_id,
        workspace_id,
    )
    head = int(head or 0)

    replay = None
    if since is not None and 0 <= since <= head:
        replay = await conn.fetch(
            """SELECT id, event, data FROM run_events
               WHERE run_id = $1::uuid AND workspace_id = $2::uuid AND id > $3
               ORDER BY id LIMIT $4""",
            run_id,
            workspace_id,
            since,
            REPLAY_MAX + 1,
        )
        if len(replay) > REPLAY_MAX:
            replay = None  # too far behind — fall back to a fresh snapshot
    if replay is not None:
        return _Opening(row, head, replay, None, None)

    nodes = await conn.fetch(
        "SELECT node_id, state FROM run_nodes "
        "WHERE run_id = $1::uuid AND workspace_id = $2::uuid ORDER BY node_id",
        run_id,
        workspace_id,
    )
    blocks = await conn.fetch(
        "SELECT block FROM run_blocks "
        "WHERE run_id = $1::uuid AND workspace_id = $2::uuid ORDER BY ord",
        run_id,
        workspace_id,
    )
    return _Opening(row, head, None, nodes, blocks)


def _snapshot_frame(opening: _Opening) -> str:
    """The authoritative recovery surface: a client that (re)connects mid-run rebuilds the
    DAG (nodes[]) and the output pane (content[], collapsed replace semantics) from this
    one frame. Its ``id:`` is the watermark everything live is measured against."""
    row = opening.row
    return _sse_frame(
        "snapshot",
        {
            "run_id": row["id"],
            "status": row["status"],
            "pending_gate": row["pending_gate"],
            "pending_content": row["pending_content"],
            "protocol": PROTOCOL,
            "nodes": [{"id": r["node_id"], "state": r["state"]} for r in opening.nodes or []],
            "content": [
                b["block"] if isinstance(b["block"], dict) else json.loads(b["block"])
                for b in opening.blocks or []
            ],
        },
        seq=opening.head,
    )


def _terminal_frame(row) -> str:
    done = {"run_id": row["id"], "status": row["status"]}
    if row["output"] is not None:
        done["output"] = row["output"]
    if row["error"] is not None:
        done["error"] = row["error"]
    if row.get("error_code") is not None:
        done["error_code"] = row["error_code"]
    return _sse_frame("done", done)


def _replay_frames(replay, chunks: bool):
    """Yield ``(frame, seq, event)`` for each replayed row, in id order.

    A resume emits the missed transitions and NOTHING else. A snapshot alongside them
    would be actively wrong: it reflects state NEWER than the replayed events, so
    applying them after it would regress node states and re-open a gate the run has
    already passed.
    """
    for event_row in replay:
        event = event_row["event"]
        data = event_row["data"]
        if isinstance(data, str):
            data = json.loads(data)
        seq = int(event_row["id"])
        if event == "chunk" and not chunks:
            continue
        yield _sse_frame(event, data, seq=seq), seq, event


async def run_event_stream(
    pool,
    workspace_id: str,
    run_id: str,
    *,
    is_disconnected: Callable[[], Awaitable[bool]],
    chunks: bool = False,
    since: int | None = None,
) -> AsyncIterator[str]:
    """Yield the run's `text/event-stream` body: `snapshot` (or a `since=` replay), then
    live frames until `done`, the client disconnects, or backpressure closes the stream."""
    # Subscribe BEFORE the opening read so no event fired during it is lost. Under
    # protocol 2 this queue also receives OTHER workers' events (the broker listener
    # injects them here), so one local registry is still the single delivery path.
    q = _subscribe(run_id)
    async with _state_lock:
        _workspace_stream_count[workspace_id] = _workspace_stream_count.get(workspace_id, 0) + 1
    # Everything at or below this id has already reached this client — folded into the
    # snapshot, or replayed. Live frames at or below it are dropped, which is what closes
    # the subscribe-then-read overlap window.
    watermark = 0
    try:
        yield "retry: 3000\n\n"

        async with workspace_scope(pool, workspace_id) as conn:
            opening = await _read_opening(conn, workspace_id, run_id, since)
        if opening is None:
            return

        if opening.replay is not None:
            for frame, seq, event in _replay_frames(opening.replay, chunks):
                watermark = seq
                yield frame
                if event == "done":
                    return
        else:
            watermark = opening.head
            yield _snapshot_frame(opening)

        # A replay that reached `done` already returned; this covers the snapshot path
        # and a resume whose `since` was already past the run's last event.
        if opening.row["status"] in _TERMINAL_STATUSES:
            yield _terminal_frame(opening.row)
            return

        async for frame in _live_frames(q, is_disconnected, chunks, watermark):
            yield frame
    finally:
        _unsubscribe(run_id, q)
        async with _state_lock:
            remaining = _workspace_stream_count.get(workspace_id, 0) - 1
            if remaining <= 0:
                _workspace_stream_count.pop(workspace_id, None)
            else:
                _workspace_stream_count[workspace_id] = remaining


async def _live_frames(
    q: asyncio.Queue,
    is_disconnected: Callable[[], Awaitable[bool]],
    chunks: bool,
    watermark: int,
) -> AsyncIterator[str]:
    """The live tail: heartbeats, backpressure close, and de-duplicated event frames."""
    while True:
        if await is_disconnected():
            return
        try:
            frame = await asyncio.wait_for(q.get(), timeout=_STREAM_HEARTBEAT_S)
        except TimeoutError:
            # A heartbeat carries NO id: line. It is not a run event, and advancing the
            # client's resume point past one would make the next ?since= skip real events.
            yield _sse_frame("ping", {"ts": _utc_now()})
            continue
        event, data = frame
        if (event, data) == _OVERFLOW_CLOSE:
            # Backpressure overflow: close rather than silently drop. Under protocol 2
            # the client reconnects with ?since=<last id> and replays what it missed
            # instead of rebuilding from scratch.
            return
        if event == "chunk" and not chunks:
            continue
        seq = getattr(frame, "seq", None)
        if seq is not None:
            if seq <= watermark:
                continue  # already folded into the snapshot or the replay
            watermark = seq
        yield _sse_frame(event, data, seq=seq)
        if event == "done":
            return
