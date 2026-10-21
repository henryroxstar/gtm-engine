"""Dispatch intents — how a human settles a send whose outcome nobody can prove (F4).

Always-on hardening PRD (2026-10-19), F4. When an external send
(an enrollment into a sequencer, a publish) times out, hits a 5xx, or its worker dies between the request and
its outcome, the platform does NOT guess: it records the outcome as *unknown* and refuses to repeat that send
until someone checks the sequencer / the platform and says what happened. These two routes are that "say".

* ``GET  /v1/dispatch-intents?state=unknown`` — the sends still waiting for a human answer.
* ``POST /v1/dispatch-intents/{batch_id}/resolve`` — ``sent`` (it went out: stays blocked), ``not_sent``
  (it did not: released), or ``sent_recipients`` (a partial send: the listed recipients are marked sent, the
  rest released).

USER-ONLY (``require_auth``): a service principal's ``sk-`` key fails JWT decode here (401) and these routes
are deliberately NOT on the ``require_principal`` allowlist — a machine that could clear its own "unknown" would
defeat the point of making a human look. Resolving never edits or deletes a row (``gtm_api`` has no UPDATE or
DELETE on the table); it appends.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from gtm_core.dispatch_intents import recipient_key

from ..deps import WorkspaceCtx, require_auth
from ..schemas import ERROR_RESPONSES
from ..services.dispatch_intents import PgIntentStore

router = APIRouter(prefix="/dispatch-intents", tags=["dispatch-intents"], responses=ERROR_RESPONSES)

_BATCH_RE = re.compile(r"^b-[0-9a-f]{12}$")


class OpenIntent(BaseModel):
    batch_id: str
    scope: str = Field(description="The sequence id, or 'publish:<platform>' for a publish.")
    tool: str
    recipients: int = Field(description="How many recipients are still waiting for an answer.")
    state: Literal["unknown"]
    since: str
    label: str | None = Field(
        default=None, description="For a publish: the post, so a human can find it."
    )


class ResolveRequest(BaseModel):
    resolution: Literal["sent", "not_sent", "sent_recipients"]
    sent_recipients: list[str] | None = Field(
        default=None,
        description="Required with 'sent_recipients': the emails (or 'id:<lead id>') that DID go out.",
    )


class ResolveResponse(BaseModel):
    sent: int
    released: int


def _store(request: Request, ws: WorkspaceCtx) -> PgIntentStore:
    return PgIntentStore(request.app.state.pool, ws.workspace_id)


@router.get("", response_model=list[OpenIntent])
async def list_open(
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
    state: Annotated[Literal["unknown"], Query()] = "unknown",
) -> list[OpenIntent]:
    """Sends waiting for a human answer ("it went out" / "it didn't"), oldest first."""
    open_batches = await _store(request, ws).unresolved()
    return [
        OpenIntent(
            batch_id=b.batch_id,
            scope=b.scope,
            tool=b.tool,
            recipients=b.count,
            state="unknown",
            since=b.ts,
            label=b.label,
        )
        for b in open_batches
    ]


@router.post("/{batch_id}/resolve", response_model=ResolveResponse)
async def resolve(
    batch_id: str,
    body: ResolveRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> ResolveResponse:
    """Record what actually happened to a send whose outcome was unknown. Appends; never edits."""
    if not _BATCH_RE.match(batch_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            {"code": "batch_not_found", "message": "No such dispatch batch."},
        )
    listed: list[str] | None = None
    if body.resolution == "sent_recipients":
        if not body.sent_recipients:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                {
                    "code": "sent_recipients_required",
                    "message": "Say which recipients went out, or use 'sent' / 'not_sent'.",
                },
            )
        try:
            listed = [
                recipient_key(r) if not r.startswith("id:") else r.strip()
                for r in body.sent_recipients
            ]
        except ValueError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                {
                    "code": "sent_recipients_invalid",
                    "message": "Every recipient needs an email or 'id:<n>'.",
                },
            ) from None
    elif body.sent_recipients:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {
                "code": "sent_recipients_unexpected",
                "message": "sent_recipients only goes with resolution 'sent_recipients'.",
            },
        )
    try:
        counts = await _store(request, ws).resolve(
            batch_id, body.resolution, sent_recipients=listed, actor=f"user:{ws.user_id}"
        )
    except KeyError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            {"code": "batch_not_found", "message": "No such dispatch batch."},
        ) from None
    return ResolveResponse(**counts)
