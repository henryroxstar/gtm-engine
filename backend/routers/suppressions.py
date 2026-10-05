"""Proactive domain and contact suppressions router (PRD-324)."""

from __future__ import annotations

import csv
import logging
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from gtm_core.ledgers import Ledgers
from gtm_core.paths import workspace_content_root
from gtm_core.suppression import Suppression, append

from ..database import workspace_scope
from ..deps import WorkspaceCtx, require_auth
from ..ratelimit import limiter
from ..schemas import (
    ERROR_RESPONSES,
    SuppressionAddRequest,
    SuppressionEntryResponse,
    SuppressionsListResponse,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/profiles", tags=["suppressions"], responses=ERROR_RESPONSES)


async def _require_profile(request: Request, ws: WorkspaceCtx, profile_name: str) -> None:
    """Ensure profile exists for this workspace under RLS."""
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM profiles WHERE workspace_id = $1::uuid AND profile_name = $2",
            ws.workspace_id,
            profile_name,
        )
        if not exists:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Profile {profile_name!r} not found")


def _suppression_file(request: Request, ws: WorkspaceCtx, profile_name: str) -> Path:
    repo_root = (
        getattr(request.app.state.cfg, "repo_root", None)
        if hasattr(request.app.state, "cfg")
        else None
    )
    content_root = workspace_content_root(ws.workspace_id, repo_root)
    return content_root / profile_name / "prospects" / ".pool" / "suppression.csv"


@router.get("/{profile_name}/suppressions", response_model=SuppressionsListResponse)
async def list_suppressions(
    profile_name: str,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    reason: str | None = None,
) -> SuppressionsListResponse:
    """List local exclusions from the suppression ledger with pagination and filtering."""
    await _require_profile(request, ws, profile_name)
    path = _suppression_file(request, ws, profile_name)

    entries: list[SuppressionEntryResponse] = []
    if path.exists():
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                r = row.get("reason", "").strip()
                if reason and r != reason:
                    continue
                entries.append(
                    SuppressionEntryResponse(
                        email=(row.get("email") or "").strip().lower(),
                        name=(row.get("name") or "").strip(),
                        company_domain=(row.get("company_domain") or "").strip().lower(),
                        reason=r,
                        date=(row.get("date") or "").strip(),
                        note=(row.get("note") or "").strip(),
                    )
                )

    total = len(entries)
    sliced = entries[offset : offset + limit]
    return SuppressionsListResponse(suppressions=sliced, total=total, limit=limit, offset=offset)


@router.post("/{profile_name}/suppressions")
@limiter.limit("30/minute")
async def add_suppressions(
    profile_name: str,
    body: SuppressionAddRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> dict[str, object]:
    """Batch append proactive domain and person exclusions to the local ledger."""
    await _require_profile(request, ws, profile_name)
    path = _suppression_file(request, ws, profile_name)

    user_id = ws.user_id or "user"
    today_iso = date.today().isoformat()
    suppressions: list[Suppression] = []

    for item in body.items:
        note_str = f"added_by:{user_id} | {item.note}" if item.note else f"added_by:{user_id}"
        suppressions.append(
            Suppression(
                email=(item.email or "").strip().lower(),
                name=(item.name or "").strip(),
                company_domain=(item.company_domain or "").strip().lower(),
                reason=item.reason,
                date=today_iso,
                note=note_str,
            )
        )

    added, skipped = append(path, suppressions)
    if added > 0:
        try:
            repo_root = (
                getattr(request.app.state.cfg, "repo_root", None)
                if hasattr(request.app.state, "cfg")
                else None
            )
            content_root = workspace_content_root(ws.workspace_id, repo_root)
            Ledgers(SimpleNamespace(content_root=content_root), profile_name).append_history(
                {
                    "event": "suppressions_added",
                    "added": added,
                    "skipped": skipped,
                    "user_id": user_id,
                }
            )
        except Exception:
            log.exception("Failed to append audit history for suppressions in %s", profile_name)

    return {"status": "ok", "added": added, "skipped": skipped}
