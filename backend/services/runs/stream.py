"""The run event stream (`GET /v1/runs/{id}/stream`) — the SSE generator itself.

Split out of the router in Phase 1b: what stays there is the HTTP shell (the per-workspace
stream cap, the 404, the ``StreamingResponse``), and what lives here is the protocol —
subscribe-before-snapshot, the protocol-1 snapshot frame, heartbeats, backpressure close,
and the accounting that must run in a ``finally`` so a dropped client always frees its slot.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable

from ...database import workspace_scope
from .events import _OVERFLOW_CLOSE, _sse_frame, _subscribe, _unsubscribe, _utc_now
from .state import _STREAM_HEARTBEAT_S, _TERMINAL_STATUSES, _state_lock, _workspace_stream_count


async def run_event_stream(
    pool,
    workspace_id: str,
    run_id: str,
    *,
    is_disconnected: Callable[[], Awaitable[bool]],
    chunks: bool = False,
) -> AsyncIterator[str]:
    """Yield the run's `text/event-stream` body: `snapshot`, then live frames until `done`,
    the client disconnects, or backpressure closes the stream."""
    # Subscribe BEFORE the snapshot read so no event fired during the read is lost.
    q = _subscribe(run_id)
    async with _state_lock:
        _workspace_stream_count[workspace_id] = _workspace_stream_count.get(workspace_id, 0) + 1
    seq = 0
    try:
        yield "retry: 3000\n\n"

        async with workspace_scope(pool, workspace_id) as conn:
            row = await conn.fetchrow(
                """SELECT id::text, status, output, error, pending_gate, pending_content
                   FROM runs WHERE id = $1::uuid AND workspace_id = $2::uuid""",
                run_id,
                workspace_id,
            )
            node_rows = await conn.fetch(
                "SELECT node_id, state FROM run_nodes "
                "WHERE run_id = $1::uuid AND workspace_id = $2::uuid ORDER BY node_id",
                run_id,
                workspace_id,
            )
            block_rows = await conn.fetch(
                "SELECT block FROM run_blocks "
                "WHERE run_id = $1::uuid AND workspace_id = $2::uuid ORDER BY ord",
                run_id,
                workspace_id,
            )
        if row is None:  # deleted between the existence check and here
            return
        # Protocol-1 snapshot: authoritative recovery surface — a client that
        # (re)connects mid-run rebuilds the DAG (nodes[]) and the output pane
        # (content[], collapsed replace semantics) from this one frame.
        yield _sse_frame(
            "snapshot",
            {
                "run_id": row["id"],
                "status": row["status"],
                "pending_gate": row["pending_gate"],
                "pending_content": row["pending_content"],
                "protocol": 1,
                "nodes": [{"id": r["node_id"], "state": r["state"]} for r in node_rows or []],
                "content": [
                    b["block"] if isinstance(b["block"], dict) else json.loads(b["block"])
                    for b in block_rows or []
                ],
            },
            seq=seq,
        )
        seq += 1

        if row["status"] in _TERMINAL_STATUSES:
            done = {"run_id": row["id"], "status": row["status"]}
            if row["output"] is not None:
                done["output"] = row["output"]
            if row["error"] is not None:
                done["error"] = row["error"]
            yield _sse_frame("done", done, seq=seq)
            return

        while True:
            if await is_disconnected():
                return
            try:
                event, data = await asyncio.wait_for(q.get(), timeout=_STREAM_HEARTBEAT_S)
            except TimeoutError:
                yield _sse_frame("ping", {"ts": _utc_now()}, seq=seq)
                seq += 1
                continue
            if (event, data) == _OVERFLOW_CLOSE:
                # Backpressure overflow: close rather than silently drop — the
                # client reconnects and resyncs from a fresh snapshot.
                return
            if event == "chunk" and not chunks:
                continue
            yield _sse_frame(event, data, seq=seq)
            seq += 1
            if event == "done":
                return
    finally:
        _unsubscribe(run_id, q)
        async with _state_lock:
            remaining = _workspace_stream_count.get(workspace_id, 0) - 1
            if remaining <= 0:
                _workspace_stream_count.pop(workspace_id, None)
            else:
                _workspace_stream_count[workspace_id] = remaining
