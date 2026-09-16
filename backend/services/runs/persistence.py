"""Run-row I/O shared by both executors: run_nodes / run_blocks upserts in the wire
vocabulary, the ``file`` content block, and the pack path's single failure exit with its
closed set of failure codes."""

from __future__ import annotations

import json
from typing import Literal, NamedTuple, get_args

from ...database import workspace_scope
from .events import publish_run_event
from .state import _TERMINAL_STATUSES

#: Why a run ended ``failed``, for a client to branch on (M-07). ``error`` stays the prose to
#: show or log. Grow it additively: a client treats an unknown code as a generic failure.
RunErrorCode = Literal[
    "cost_cap_reached",
    "gate_timeout",
    "run_interrupted",
    "retries_exhausted",
    "fake_runs_disabled",
    "worker_unavailable",
    "publish_not_configured",
    "email_not_configured",
    "publish_draft_missing",
    "disclosure_required",
    "draft_integrity_failed",
    "draft_invalid",
    "dispatch_failed",
    "node_failed",
    "internal_error",
]
RUN_ERROR_CODES: frozenset[str] = frozenset(get_args(RunErrorCode))


class RunFailure(NamedTuple):
    """A failure produced away from its ``_fail_run`` call, carrying its code from the source."""

    code: RunErrorCode
    error: str


#: The §R2 refusal every executor reports as ``cost_cap_reached``.
_CAP_REACHED = "monthly cost cap reached"

#: RL-03: `_fail_run`'s own terminal-row guard — same shape as `.lifecycle._NOT_TERMINAL_SQL`
#: (and `.decisions.cancel`'s), derived from `_TERMINAL_STATUSES` so a fifth terminal status
#: needs one edit, not six.
_NOT_TERMINAL_SQL = (
    "status NOT IN (" + ", ".join(f"'{s}'" for s in sorted(_TERMINAL_STATUSES)) + ")"
)

#: A refused publish dispatch's own status (``agent.publish_dispatch.DispatchOutcome``).
_PUBLISH_REFUSAL_CODES: dict[str, RunErrorCode] = {
    "hash_mismatch": "draft_integrity_failed",
    "disclosure_missing": "disclosure_required",
}

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


def enroll_refusal(outcome) -> RunFailure | None:
    """Why an approved enrollment dispatch fails the run, or None. ``outcome`` is
    ``dispatch_backend_email_enroll``'s return: None when the workspace has no Saleshandy key
    (or the dispatch swallowed an internal exception) — nothing was enrolled. A dry run is not
    a failure."""
    if outcome is None:
        return RunFailure(
            "email_not_configured",
            "no Saleshandy API key configured for this workspace — not enrolled",
        )
    if outcome.status != "dry_run" and not outcome.ok:
        return RunFailure("dispatch_failed", outcome.operator_line())
    return None


def publish_refusal(outcome) -> RunFailure | None:
    """Why an approved publish dispatch fails the run, or None. ``outcome`` is
    ``dispatch_backend_publish``'s return."""
    if outcome is None:
        # No enabled destination for this workspace (or dispatch_backend_publish swallowed an
        # internal exception) — nothing was sent. Without this the gated node still flipped to
        # "complete", marking the run as if the post went out even though dispatch never happened.
        return RunFailure(
            "publish_not_configured",
            "no publish destination configured for this workspace — not published",
        )
    # Any non-success outcome fails the run, EXCEPT: dry_run (a structurally intentional no-op —
    # see agent/publish_dispatch.py) and a "duplicate" publish (the A5 gate-decision-replay path
    # re-dispatching bytes already sent before a restart — idempotency, not failure). Every other
    # status — hash_mismatch, disclosure_missing, and every LinkedInPublisher failure mode
    # collapsed under "publish_failed" (disabled/schedule_disabled/misconfigured/invalid/
    # rate_limited/error) — must not silently read as a successful publish.
    already_published = outcome.result is not None and outcome.result.status == "duplicate"
    if outcome.status != "dry_run" and not outcome.ok and not already_published:
        code = _PUBLISH_REFUSAL_CODES.get(outcome.status, "dispatch_failed")
        return RunFailure(code, outcome.operator_line())
    return None


async def _fail_run(
    pool, workspace_id: str, run_id: str, error: str, *, error_code: RunErrorCode
) -> None:
    """Persist a failed terminal state + emit `done` — the pack path's one failure exit.

    RL-03: guarded against a terminal row — a stray failure racing a cancel (or any
    other terminal write from another worker) must not overwrite it. The `done` frame
    is emitted only when the write actually matched."""
    if error_code not in RUN_ERROR_CODES:
        raise ValueError(f"unknown run error_code {error_code!r}")
    async with workspace_scope(pool, workspace_id) as conn:
        updated = await conn.fetchrow(
            f"UPDATE runs SET status = 'failed', error = $2, error_code = $3, completed_at = now(), "  # nosec B608 — status list is a module constant, never external input
            f"pending_gate = NULL, pending_content = NULL "
            f"WHERE id = $1::uuid AND {_NOT_TERMINAL_SQL} RETURNING id",
            run_id,
            error,
            error_code,
        )
    if updated is None:
        return
    publish_run_event(
        workspace_id,
        run_id,
        "done",
        {"run_id": run_id, "status": "failed", "error": error, "error_code": error_code},
    )
