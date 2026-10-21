"""Workspace-level events feed (PRD §3 G3).

  GET /v1/events/stream  → workspace-scoped SSE stream over run-event.schema.json

Pull-only fan-in over all runs in the authenticated workspace. Deliberately not a
webhook (§R6 / PRD §3 G3): pull-only keeps the destination set empty, avoiding
tenant-supplied webhook targets and new backend egress.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from ..callers.limits import enforce_principal_rate
from ..callers.principal import Principal
from ..callers.rest import require_principal
from ..schemas import ERROR_RESPONSES
from ..services.runs.principal_admission import read_scope_for_principal
from ..services.runs.state import _MAX_STREAMS_PER_WORKSPACE, _workspace_stream_count
from ..services.runs.stream import workspace_event_stream

router = APIRouter(prefix="/events", tags=["events"], responses=ERROR_RESPONSES)


@router.get("/stream")
async def stream_workspace_events(
    principal: Annotated[Principal, Depends(require_principal)],
    request: Request,
    _rate: Annotated[None, Depends(enforce_principal_rate)] = None,
) -> StreamingResponse:
    """Stream workspace-level run progress as Server-Sent Events (text/event-stream).

    Pull-only workspace fan-in: on connect emits one `snapshot` frame for each
    non-terminal run in the workspace, then live frames as runs progress. Every frame
    carries `run_id`.

    Narrowed to the calling agent's own runs when a `service` principal's bound agent
    has `read_scope = 'own'`; `read_scope = 'workspace'` or a `user` principal sees all
    runs in the workspace.
    """
    pool = request.app.state.pool
    ws = principal.workspace_id

    if _workspace_stream_count.get(ws, 0) >= _MAX_STREAMS_PER_WORKSPACE:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            {
                "code": "too_many_streams",
                "message": "Too many open streams for this workspace",
                "max": _MAX_STREAMS_PER_WORKSPACE,
            },
        )

    read_scope = "own"
    if principal.kind == "service":
        read_scope = await read_scope_for_principal(pool, ws, principal)

    return StreamingResponse(
        workspace_event_stream(
            pool,
            ws,
            principal=principal,
            read_scope=read_scope,
            is_disconnected=request.is_disconnected,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
