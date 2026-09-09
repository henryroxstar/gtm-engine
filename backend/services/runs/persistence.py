"""Run-row I/O shared by both executors: run_nodes / run_blocks upserts in the wire
vocabulary, the ``file`` content block, and the pack path's single failure exit."""

from __future__ import annotations

import json

from ...database import workspace_scope
from .events import publish_run_event

# Manifest stage status → wire node state (run-event.schema.json node.state enum).
# A gate-paused node reads 'running' on the wire — the awaiting_approval event
# carries the gate detail; 'awaiting_approval' is a RUN status, not a node state.
_WIRE_NODE_STATE = {
    "pending": "queued",
    "running": "running",
    "ok": "completed",
    "failed": "failed",
    "skipped": "skipped",
    "awaiting_approval": "running",
}


async def _persist_node(pool, workspace_id: str, run_id: str, node_id: str, entry: dict) -> str:
    """Upsert one run_nodes row (wire vocabulary) and return the wire state."""
    state = _WIRE_NODE_STATE.get(entry.get("status", ""), "running")
    async with workspace_scope(pool, workspace_id) as conn:
        await conn.execute(
            """INSERT INTO run_nodes(run_id, workspace_id, node_id, state, error,
                                     started_at, finished_at)
               VALUES($1::uuid, $2::uuid, $3, $4, $5,
                      COALESCE($6::timestamptz, now()),
                      CASE WHEN $4 IN ('completed','failed','skipped')
                           THEN now() ELSE NULL END)
               ON CONFLICT (run_id, node_id) DO UPDATE
                 SET state = EXCLUDED.state,
                     error = EXCLUDED.error,
                     finished_at = EXCLUDED.finished_at""",
            run_id,
            workspace_id,
            node_id,
            state,
            entry.get("error"),
            entry.get("started"),
        )
    return state


async def _upsert_block(
    pool, workspace_id: str, run_id: str, block_id: str, node_id: str | None, block: dict
) -> None:
    """Upsert one content block, assigning first-insert ord (snapshot order)."""
    async with workspace_scope(pool, workspace_id) as conn:
        await conn.execute(
            """INSERT INTO run_blocks(run_id, workspace_id, block_id, ord, node_id, block)
               VALUES($1::uuid, $2::uuid, $3,
                      COALESCE((SELECT max(ord) + 1 FROM run_blocks
                                WHERE run_id = $1::uuid AND workspace_id = $2::uuid), 0),
                      $4, $5::jsonb)
               ON CONFLICT (run_id, block_id) DO UPDATE
                 SET block = EXCLUDED.block, updated_at = now()""",
            run_id,
            workspace_id,
            block_id,
            node_id,
            json.dumps(block, ensure_ascii=False),
        )


def _file_block(artifact_id: str, art) -> dict:
    """A `file` content block (run-event contract): artifact_id only — never a URL;
    fallback_text mandatory so no client drops the deliverable silently."""
    return {
        "id": f"file-{artifact_id}",
        "type": "file",
        "props": {
            "artifact_id": artifact_id,
            "name": art.name,
            "size_bytes": art.size_bytes,
            "media_type": art.media_type,
            "sha256": art.sha256,
        },
        "fallback_text": f"Attachment: {art.name} ({art.size_bytes} bytes)",
    }


async def _fail_run(pool, workspace_id: str, run_id: str, error: str) -> None:
    """Persist a failed terminal state + emit `done` — the pack path's one failure exit."""
    async with workspace_scope(pool, workspace_id) as conn:
        await conn.execute(
            "UPDATE runs SET status = 'failed', error = $2, completed_at = now() "
            "WHERE id = $1::uuid",
            run_id,
            error,
        )
    publish_run_event(
        workspace_id, run_id, "done", {"run_id": run_id, "status": "failed", "error": error}
    )
