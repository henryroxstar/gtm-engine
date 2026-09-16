"""Onboarding endpoints — POST /onboard, POST /{draft_id}/product/{slug}/extract,
GET /{draft_id}/diff, POST /{draft_id}/promote, DELETE /{draft_id}.

All endpoints require JWT auth (require_auth). Staging directories are keyed on
draft_id (UUID). Slug is derived from the brain's ProfileDraft — never from a
user-controlled path component.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
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
from backend.types import UuidStr
from gtm_core.ingest import (
    OnboardingCapReachedError,
    UrlIngestFailedError,
    UrlIngestUnavailableError,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/onboard", tags=["onboard"])

# In-memory draft registry — maps draft_id → {slug, staged_root, draft}.
# Single-process safe. Replace with DB-backed storage for multi-process deployments.
_drafts: dict[str, dict] = {}


async def _get_cfg(request: Request, ws: WorkspaceCtx):
    """Config scoped to the caller's workspace tree (P3 filesystem isolation), so
    staged drafts + promoted profiles land under ``data/workspaces/<ws>/`` — never
    the shared (single-tenant) ``profiles/`` tree."""
    from backend.services.integrations import get_workspace_credentials
    from backend.session import _workspace_scoped_config

    base = request.app.state.cfg
    creds = await get_workspace_credentials(request.app.state.pool, str(ws.workspace_id))
    return _workspace_scoped_config(base, str(ws.workspace_id), base.repo_root, credentials=creds)


@contextmanager
def _onboarding_errors() -> Iterator[None]:
    """Map the onboarding producers' typed failures onto the envelope.

    Only an ``OnboardingInputError`` is the caller's to fix (422, its message shown). A
    service condition is a 503 and an unusable model or crawl response a 502, each with a
    fixed message: their detail names env vars, spend, or model output, so it is logged only.
    Any other exception is not relabelled and reaches the 500 envelope.

    Each raise is a direct ``HTTPException(...)`` call, not routed through a shared helper —
    the error-code census (``tests/contracts/test_error_code_census.py``) statically resolves
    a literal status/detail at the call site itself, and cannot see through a wrapper whose
    status/code are its own parameters.
    """
    from agent.onboard import OnboardingExtractError, OnboardingInputError

    try:
        yield
    except OnboardingInputError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "onboarding_input_invalid", "message": str(exc)},
        ) from exc
    except UrlIngestUnavailableError as exc:
        log.warning("onboarding URL ingest is not configured: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "url_ingest_unavailable",
                "message": "Importing from a URL is unavailable right now. Paste the text instead.",
            },
        ) from exc
    except OnboardingCapReachedError as exc:
        log.warning("onboarding spend cap reached: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "onboarding_cap_reached",
                "message": "Onboarding is unavailable right now. Try again later.",
            },
        ) from exc
    except UrlIngestFailedError as exc:
        log.warning("onboarding URL ingest got an unusable response: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "url_ingest_failed",
                "message": "The page could not be imported right now. Try again later.",
            },
        ) from exc
    except OnboardingExtractError as exc:
        log.warning("onboarding extraction produced an unusable draft: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "onboarding_extract_failed",
                "message": "The company details could not be extracted right now. Try again later.",
            },
        ) from exc


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

    from agent.onboard import OnboardingExtractError, extract, render, slugify, stage
    from agent.onboard import ingest as do_ingest

    cfg = await _get_cfg(request, ws)

    with _onboarding_errors():
        raw_text = await asyncio.to_thread(do_ingest, body.source, body.source_type, cfg)
        draft = await extract(raw_text, cfg)
        try:
            slug = slugify(draft["company"]["name"])
        except ValueError as exc:
            raise OnboardingExtractError(f"extracted company name yields no slug: {exc}") from exc
        files = render(draft)
        draft_id, staged_root = await asyncio.to_thread(
            stage, slug, files, cfg, draft["company"]["name"]
        )

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
    draft_id: UuidStr,
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

    cfg = await _get_cfg(request, ws)

    with _onboarding_errors():
        extra_text = await asyncio.to_thread(do_ingest, body.source, body.source_type, cfg)
        updated_draft = await extract_product(product_slug, extra_text, entry["draft"], cfg)

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
    draft_id: UuidStr,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> OnboardDiffResponse:
    """Return per-file diffs between staged files and the live profile."""
    import asyncio

    from agent.onboard import diff

    entry = _get_owned_draft(draft_id, ws)

    cfg = await _get_cfg(request, ws)
    raw_diffs = await asyncio.to_thread(diff, entry["slug"], entry["staged_root"], cfg)

    return OnboardDiffResponse(
        draft_id=draft_id,
        slug=entry["slug"],
        diffs={k: FileDiff(**v) for k, v in raw_diffs.items()},
    )


@router.post("/{draft_id}/promote", status_code=status.HTTP_200_OK)
async def promote_endpoint(
    draft_id: UuidStr,
    body: OnboardPromoteRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> dict:
    """Promote the staged profile bundle to profiles/<slug>/."""
    import asyncio

    from agent.onboard import DraftNotStagedError, ProfileAlreadyExistsError, promote

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

    cfg = await _get_cfg(request, ws)
    try:
        live_dir = await asyncio.to_thread(
            promote, slug, draft_id, entry["staged_root"], draft, cfg
        )
    except ProfileAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "profile_already_exists",
                "message": "A profile with this name already exists in this workspace.",
                "slug": slug,
            },
        ) from exc
    except DraftNotStagedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "draft_not_staged",
                "message": "This draft is no longer staged. Start onboarding again.",
            },
        ) from exc

    _drafts.pop(draft_id, None)

    return {"slug": slug, "profile_path": str(live_dir), "status": "promoted"}


@router.delete("/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_endpoint(
    draft_id: UuidStr,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
) -> None:
    """Cancel and clean up a staged onboarding draft."""
    from agent.onboard import cancel

    entry = _get_owned_draft(draft_id, ws)
    _drafts.pop(draft_id, None)

    cancel(entry["staged_root"])
