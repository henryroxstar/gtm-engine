"""Onboarding endpoints — POST /ingest, /product, GET /diff, POST /promote, DELETE /cancel.

All endpoints require JWT auth (require_auth). Staging directories are keyed on
draft_id (UUID). Slug is derived from the brain's ProfileDraft — never from a
user-controlled path component.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from backend.deps import WorkspaceCtx, require_auth
from backend.ratelimit import limiter
from backend.schemas import (
    FileDiff,
    OnboardDiffResponse,
    OnboardIngestRequest,
    OnboardIngestResponse,
    OnboardProductExtractRequest,
    OnboardPromoteRequest,
)

router = APIRouter(prefix="/onboard", tags=["onboard"])

# In-memory draft registry — maps draft_id → {slug, staged_root, draft}.
# Single-process safe. Replace with DB-backed storage for multi-process deployments.
_drafts: dict[str, dict] = {}


def _get_cfg(request: Request, ws: WorkspaceCtx):
    """Config scoped to the caller's workspace tree (P3 filesystem isolation), so
    staged drafts + promoted profiles land under ``data/workspaces/<ws>/`` — never
    the shared (single-tenant) ``profiles/`` tree."""
    from backend.session import _workspace_scoped_config

    base = request.app.state.cfg
    return _workspace_scoped_config(base, ws.workspace_id, base.repo_root)


def _get_owned_draft(draft_id: str, ws: WorkspaceCtx) -> dict:
    """Fetch a staged draft, enforcing it belongs to the caller's workspace (B2).

    Drafts are process-global keyed by draft_id; without this check any workspace
    could read/promote/cancel another tenant's staged onboarding. 404 (not 403) so
    draft existence is not leaked across tenants.
    """
    entry = _drafts.get(draft_id)
    if not entry or entry.get("workspace_id") != ws.workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")
    return entry


@router.post("", status_code=status.HTTP_201_CREATED, response_model=OnboardIngestResponse)
@limiter.limit("10/minute")
async def ingest_endpoint(
    body: OnboardIngestRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> OnboardIngestResponse:
    """Ingest a URL/file/text and produce a staged ProfileDraft.

    - ingest() is synchronous (httpx/filesystem) → run in thread
    - extract() is async → await directly
    - render() + stage() are synchronous → run in thread
    """
    import asyncio

    from agent.onboard import extract, render, slugify, stage
    from agent.onboard import ingest as do_ingest

    cfg = _get_cfg(request, ws)

    try:
        raw_text = await asyncio.to_thread(do_ingest, body.source, body.source_type, cfg)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    try:
        draft = await extract(raw_text, cfg)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    slug = slugify(draft["company"]["name"])

    try:
        files = render(draft)
        draft_id, staged_root = await asyncio.to_thread(
            stage, slug, files, cfg, draft["company"]["name"]
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    _drafts[draft_id] = {
        "slug": slug,
        "staged_root": staged_root,
        "draft": draft,
        "workspace_id": ws.workspace_id,
    }

    return OnboardIngestResponse(
        draft_id=draft_id,
        slug=slug,
        staged_files=list(files.keys()),
        confidence=draft.get("confidence", "unknown"),
        gaps=draft.get("gaps", []),
    )


@router.post(
    "/{draft_id}/product/{product_slug}/extract",
    response_model=OnboardIngestResponse,
)
@limiter.limit("10/minute")
async def re_extract_product_endpoint(
    draft_id: str,
    product_slug: str,
    body: OnboardProductExtractRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> OnboardIngestResponse:
    """Re-extract one product with an additional source URL/file/text."""
    import asyncio

    from agent.onboard import cancel, extract_product, render, stage
    from agent.onboard import ingest as do_ingest

    entry = _get_owned_draft(draft_id, ws)

    cfg = _get_cfg(request, ws)

    try:
        extra_text = await asyncio.to_thread(do_ingest, body.source, body.source_type, cfg)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    try:
        updated_draft = await extract_product(product_slug, extra_text, entry["draft"], cfg)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    slug = entry["slug"]
    cancel(entry["staged_root"])

    files = render(updated_draft)
    new_draft_id, new_staged_root = await asyncio.to_thread(
        stage, slug, files, cfg, updated_draft["company"]["name"]
    )

    _drafts.pop(draft_id, None)
    _drafts[new_draft_id] = {
        "slug": slug,
        "staged_root": new_staged_root,
        "draft": updated_draft,
        "workspace_id": ws.workspace_id,
    }

    return OnboardIngestResponse(
        draft_id=new_draft_id,
        slug=slug,
        staged_files=list(files.keys()),
        confidence=updated_draft.get("confidence", "unknown"),
        gaps=updated_draft.get("gaps", []),
    )


@router.get("/{draft_id}/diff", response_model=OnboardDiffResponse)
async def get_diff_endpoint(
    draft_id: str,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> OnboardDiffResponse:
    """Return per-file diffs between staged files and the live profile."""
    import asyncio

    from agent.onboard import diff

    entry = _get_owned_draft(draft_id, ws)

    cfg = _get_cfg(request, ws)
    raw_diffs = await asyncio.to_thread(diff, entry["slug"], entry["staged_root"], cfg)

    return OnboardDiffResponse(
        draft_id=draft_id,
        slug=entry["slug"],
        diffs={k: FileDiff(**v) for k, v in raw_diffs.items()},
    )


@router.post("/{draft_id}/promote", status_code=status.HTTP_200_OK)
async def promote_endpoint(
    draft_id: str,
    body: OnboardPromoteRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> dict:
    """Promote the staged profile bundle to profiles/<slug>/."""
    import asyncio

    from agent.onboard import promote

    entry = _get_owned_draft(draft_id, ws)

    slug = entry["slug"]
    draft = entry["draft"]

    # Tenant safeguard: operator must confirm the company name exactly (PRD §7)
    expected = draft["company"]["name"]
    if body.confirmed_company_name.strip().lower() != expected.strip().lower():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Company name mismatch: expected {expected!r}, got {body.confirmed_company_name!r}"
            ),
        )

    cfg = _get_cfg(request, ws)
    try:
        live_dir = await asyncio.to_thread(
            promote, slug, draft_id, entry["staged_root"], draft, cfg
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    _drafts.pop(draft_id, None)

    return {"slug": slug, "profile_path": str(live_dir), "status": "promoted"}


@router.delete("/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_endpoint(
    draft_id: str,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
) -> None:
    """Cancel and clean up a staged onboarding draft."""
    from agent.onboard import cancel

    entry = _get_owned_draft(draft_id, ws)
    _drafts.pop(draft_id, None)

    cancel(entry["staged_root"])
