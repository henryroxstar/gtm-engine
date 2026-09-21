"""The run event stream (`GET /v1/runs/{id}/stream`) — the SSE generator itself.

Split out of the router in Phase 1b: what stays there is the HTTP shell (the per-workspace
stream cap, the 404, the ``StreamingResponse``), and what lives here is the protocol —
subscribe-before-snapshot, the snapshot frame, heartbeats, backpressure close, the in-place
resync snapshot (ST-16), and the accounting that must run in a ``finally`` so a dropped
client always frees its slot.

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
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

from ...callers.principal import Principal
from ...database import workspace_scope
from .events import (
    _OVERFLOW_CLOSE,
    _RESYNC,
    _sse_frame,
    _subscribe,
    _subscribe_workspace,
    _Subscription,
    _unsubscribe,
    _unsubscribe_workspace,
    _utc_now,
)
from .gates import _content_sha
from .queries import _wire_node
from .state import _STREAM_HEARTBEAT_S, _TERMINAL_STATUSES, _state_lock, _workspace_stream_count

log = logging.getLogger(__name__)

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
        # ST-01: the same open-run_gates subquery _RUN_DETAIL_SQL (queries.py) already
        # uses, so a client landing on the snapshot path while a gate is open learns its
        # kind and node without a supplementary GET.
        """SELECT r.id::text, r.status, r.output, r.error, r.error_code,
                  r.pending_gate, r.pending_content, r.payload,
                  r.principal_kind, r.principal_id, r.external_ref, r.agent_id::text AS agent_id,
                  (SELECT g.gate FROM run_gates g WHERE g.run_id = r.id AND g.state = 'open'
                   ORDER BY g.opened_at DESC LIMIT 1) AS gate_kind,
                  (SELECT g.node_id FROM run_gates g WHERE g.run_id = r.id AND g.state = 'open'
                   ORDER BY g.opened_at DESC LIMIT 1) AS gate_node_id
           FROM runs r WHERE r.id = $1::uuid AND r.workspace_id = $2::uuid""",
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
        "SELECT node_id, state, error FROM run_nodes "
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


def _snapshot_payload(opening: _Opening) -> dict:
    row = opening.row
    from .queries import _payload

    payload = _payload(row)
    return {
        "run_id": row["id"],
        "status": row["status"],
        "pack": payload.get("pack"),
        "variant": payload.get("variant"),
        "inputs": payload.get("inputs"),
        "pending_gate": row["pending_gate"],
        "pending_content": row["pending_content"],
        "protocol": PROTOCOL,
        # ST-01: the open gate's identity — None once decided or on a run never gated.
        "gate": row.get("gate_kind"),
        "pending_node_id": row.get("gate_node_id"),
        "pending_content_sha": _content_sha(row["pending_content"]),
        "nodes": [_wire_node(r) for r in opening.nodes or []],
        "content": [
            b["block"] if isinstance(b["block"], dict) else json.loads(b["block"])
            for b in opening.blocks or []
        ],
        # Fleet Phase A (V026, additive): who started this run. .get: tolerate
        # fakes/fixtures that model only the pre-Fleet-Phase-A columns.
        "principal_kind": row.get("principal_kind"),
        "principal_id": row.get("principal_id"),
        "external_ref": row.get("external_ref"),
    }


def _snapshot_frame(opening: _Opening, *, seq: int | None = None) -> str:
    """The authoritative recovery surface: a client that (re)connects mid-run rebuilds the
    DAG (nodes[]) and the output pane (content[], collapsed replace semantics) from this
    one frame. Its ``id:`` is the watermark everything live is measured against."""
    return _sse_frame(
        "snapshot",
        _snapshot_payload(opening),
        seq=opening.head if seq is None else seq,
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

    async def reopen(since: int | None = None) -> _Opening | None:
        async with workspace_scope(pool, workspace_id) as conn:
            return await _read_opening(conn, workspace_id, run_id, since)

    try:
        yield "retry: 3000\n\n"

        opening = await reopen(since)
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

        async for frame in _live_frames(q, is_disconnected, chunks, watermark, reopen):
            yield frame
    finally:
        _unsubscribe(run_id, q)
        async with _state_lock:
            remaining = _workspace_stream_count.get(workspace_id, 0) - 1
            if remaining <= 0:
                _workspace_stream_count.pop(workspace_id, None)
            else:
                _workspace_stream_count[workspace_id] = remaining


async def _resync_frames(
    reopen: Callable[[], Awaitable[_Opening | None]],
) -> tuple[list[str], int, bool] | None:
    """ST-16: ``(frames, new watermark, stream finished)`` for an in-place snapshot, or
    None when the read failed — the blip that lost the event can fail this read too."""
    try:
        opening = await reopen()
    except Exception:  # noqa: BLE001 — the caller keeps the resync pending and retries
        log.warning("stream resync read failed; retrying", exc_info=True)
        return None
    if opening is None:
        return [], 0, True
    frames = [_snapshot_frame(opening)]
    finished = opening.row["status"] in _TERMINAL_STATUSES
    if finished:
        frames.append(_terminal_frame(opening.row))
    return frames, opening.head, finished


async def _live_frames(
    q: _Subscription,
    is_disconnected: Callable[[], Awaitable[bool]],
    chunks: bool,
    watermark: int,
    reopen: Callable[[], Awaitable[_Opening | None]],
) -> AsyncIterator[str]:
    """The live tail: heartbeats, backpressure close, ST-16 resync, and de-duplicated
    event frames.

    A resync (an event of this run got no durable id) sends a fresh snapshot in place.
    Until it has been sent, every dequeued frame is discarded rather than delivered: each
    was persisted before it was queued, so the snapshot read that follows folds it in, and
    delivering one first would move the client's resume point past the lost event.
    """
    while True:
        if await is_disconnected():
            return
        if q.resync:
            q.resync = False  # cleared BEFORE the read: a loss during it re-arms the flag
            resynced = await _resync_frames(reopen)
            if resynced is None:
                q.resync = True
            else:
                frames, watermark, finished = resynced
                for frame in frames:
                    yield frame
                if finished:
                    return
        try:
            frame = await asyncio.wait_for(q.get(), timeout=_STREAM_HEARTBEAT_S)
        except TimeoutError:
            # A heartbeat carries NO id: line. It is not a run event, and advancing the
            # client's resume point past one would make the next ?since= skip real events.
            yield _sse_frame("ping", {"ts": _utc_now()})
            continue
        event, data = frame
        if q.resync or (event, data) == _RESYNC:
            continue
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


async def workspace_event_stream(
    pool,
    workspace_id: str,
    *,
    principal: Principal,
    read_scope: str = "own",
    is_disconnected: Callable[[], Awaitable[bool]],
) -> AsyncIterator[str]:
    """Yield the workspace-level `text/event-stream` feed (GET /v1/events/stream).

    Fleet PRD §3 G3 invariants:
    - Pull-only SSE feed over run-event.schema.json vocabulary.
    - Every frame carries run_id.
    - Snapshot-based reconnect contract: per-connection seq, no replay, snapshot is authoritative.
    - On connect: emits one snapshot frame for each non-terminal run in the workspace.
    - Live tail: delivers events as they occur across runs in the workspace.
    - Narrowed by caller's read_scope if service principal (only its own runs if 'own').
    - Clean shutdown & backpressure handling.
    """
    q = _subscribe_workspace(workspace_id)
    async with _state_lock:
        _workspace_stream_count[workspace_id] = _workspace_stream_count.get(workspace_id, 0) + 1

    conn_seq = 0
    try:
        yield "retry: 3000\n\n"

        # Query all active/non-terminal runs in the workspace
        async with workspace_scope(pool, workspace_id) as conn:
            if principal.kind == "service" and read_scope != "workspace":
                rows = await conn.fetch(
                    """SELECT id::text, agent_id::text FROM runs
                       WHERE workspace_id = $1::uuid
                         AND status NOT IN ('ok', 'failed', 'rejected', 'canceled')
                         AND agent_id = $2::uuid
                       ORDER BY created_at ASC""",
                    workspace_id,
                    principal.agent_id,
                )
            else:
                rows = await conn.fetch(
                    """SELECT id::text, agent_id::text FROM runs
                       WHERE workspace_id = $1::uuid
                         AND status NOT IN ('ok', 'failed', 'rejected', 'canceled')
                       ORDER BY created_at ASC""",
                    workspace_id,
                )
            active_runs = [dict(r) for r in rows]

        # Emit one snapshot per non-terminal run
        for r_info in active_runs:
            run_id = r_info["id"]
            async with workspace_scope(pool, workspace_id) as conn:
                opening = await _read_opening(conn, workspace_id, run_id, None)
            if opening is not None:
                conn_seq += 1
                yield _sse_frame("snapshot", _snapshot_payload(opening), seq=conn_seq)

        # Live tail
        while True:
            if await is_disconnected():
                return
            try:
                frame = await asyncio.wait_for(q.get(), timeout=_STREAM_HEARTBEAT_S)
            except TimeoutError:
                yield _sse_frame("ping", {"ts": _utc_now()})
                continue

            event, data = frame
            if (event, data) == _OVERFLOW_CLOSE:
                return

            # Check if this frame belongs to a run the principal is allowed to read
            if principal.kind == "service" and read_scope != "workspace":
                run_id = data.get("run_id")
                if run_id:
                    async with workspace_scope(pool, workspace_id) as conn:
                        row = await conn.fetchrow(
                            "SELECT agent_id::text FROM runs WHERE id = $1::uuid AND workspace_id = $2::uuid",
                            run_id,
                            workspace_id,
                        )
                    if row is None or row["agent_id"] != principal.agent_id:
                        continue

            conn_seq += 1
            yield _sse_frame(event, data, seq=conn_seq)

    finally:
        _unsubscribe_workspace(workspace_id, q)
        async with _state_lock:
            remaining = _workspace_stream_count.get(workspace_id, 0) - 1
            if remaining <= 0:
                _workspace_stream_count.pop(workspace_id, None)
            else:
                _workspace_stream_count[workspace_id] = remaining
