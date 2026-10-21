"""Onboarding endpoints — POST /onboard, POST /{draft_id}/product/{slug}/extract,
GET /jobs/{job_id}, GET /{draft_id}/diff, POST /{draft_id}/promote, DELETE /{draft_id}.

All endpoints require JWT auth (require_auth). Staging directories are keyed on
draft_id (UUID). Slug is derived from the brain's ProfileDraft — never from a
user-controlled path component.

The two POSTs that read a source answer 202 with a job and do the work in the background
(``backend.services.onboard_jobs``, issue #259); the client polls ``GET /jobs/{job_id}``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from backend.database import workspace_scope
from backend.deps import WorkspaceCtx, require_auth
from backend.profile_rows import register_promoted_profile
from backend.ratelimit import limiter
from backend.schemas import (
    ERROR_RESPONSES,
    FileDiff,
    OnboardDiffResponse,
    OnboardIngestRequest,
    OnboardIngestResponse,
    OnboardJobResponse,
    OnboardProductExtractRequest,
    OnboardPromoteRequest,
)
from backend.services import onboard_jobs
from backend.services.runs.fake import fake_runs_enabled
from backend.types import UuidStr
from gtm_core.ingest import (
    OnboardingCapReachedError,
    UrlIngestFailedError,
    UrlIngestInvalidUrlError,
    UrlIngestTimeoutError,
    UrlIngestUnavailableError,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/onboard", tags=["onboard"], responses=ERROR_RESPONSES)

# Per-process cache of draft_id → {slug, staged_root, draft, workspace_id}. It is NOT the
# record: stage() persists the draft as ``.draft.json`` beside ``.onboard-meta.json`` in the
# caller's workspace-scoped staging tree, and _get_owned_draft rebuilds a missing entry from
# there — so a draft survives a restart/redeploy and resolves on any worker sharing the
# data volume.
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
    except (OnboardingInputError, UrlIngestInvalidUrlError) as exc:
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
    except UrlIngestTimeoutError as exc:
        log.warning("onboarding URL ingest timed out: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={
                "code": "url_ingest_timeout",
                "message": "The site took too long to import. Try again, or paste the text instead.",
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


def _draft_from_disk(draft_id: str, ws: WorkspaceCtx, cfg) -> dict | None:
    """Rebuild a registry entry from the caller's own staging tree, or ``None``.

    ``cfg`` is the workspace-scoped config from ``_get_cfg``, so the only tree searched is
    ``data/workspaces/<caller>/profiles/.staging`` — another tenant's drafts are not in it.
    Every way this can fail is the same ``None``: the caller learns nothing about why.
    """
    from agent.onboard import _STAGING_DIR, _staged_root_for_draft_id, slugify
    from gtm_core.confine import ConfinementError, confined_dir

    staging = cfg.profiles_root / _STAGING_DIR
    try:
        found = _staged_root_for_draft_id(draft_id, cfg)
        # The dir name comes from disk and becomes the live profile's directory name on
        # promote: it must be a slug stage() could have written, and resolve inside staging.
        slug = found.name
        if slugify(slug) != slug:
            return None
        staged_root = staging / slug
        confined_dir(staging, content_root=cfg.profiles_root)
        confined_dir(staged_root, content_root=staging)
        draft = json.loads((staged_root / ".draft.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, ConfinementError):
        # FileNotFoundError is an OSError; JSONDecodeError and UnicodeDecodeError are
        # ValueErrors, as is slugify's refusal of an unsafe or reserved segment.
        return None

    company = draft.get("company") if isinstance(draft, dict) else None
    name = company.get("name") if isinstance(company, dict) else None
    if not isinstance(name, str) or not name.strip():
        return None
    return {
        "slug": slug,
        "staged_root": staged_root,
        "draft": draft,
        "workspace_id": ws.workspace_id,
    }


def _get_owned_draft(draft_id: str, ws: WorkspaceCtx, cfg) -> dict:
    """Fetch a staged draft, enforcing it belongs to the caller's workspace (B2).

    The memory cache is process-global keyed by draft_id; without the workspace check any
    workspace could read/promote/cancel another tenant's staged onboarding. A cache entry
    owned by another workspace is a 404 outright — it never falls through to disk. A cache
    miss (a restart, or another worker) is resolved from the caller's own staging tree only.
    404 (not 403) so draft existence is not leaked across tenants.
    """
    entry = _drafts.get(draft_id)
    if entry is None:
        entry = _draft_from_disk(draft_id, ws, cfg)
        if entry is not None:
            _drafts[draft_id] = entry
    if not entry or entry.get("workspace_id") != ws.workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")
    return entry


async def _read_source(source: str, source_type: str, cfg) -> str:
    """The source's raw text. Under ``GTM_FAKE_RUNS`` a URL is scripted (no crawl, no key)
    and the scripted delay runs here, once per job."""
    import asyncio

    from agent.onboard import ingest as do_ingest
    from backend.services.onboard_fake import fake_delay_s, fake_page_text

    if not fake_runs_enabled():
        return await asyncio.to_thread(do_ingest, source, source_type, cfg)
    await asyncio.sleep(fake_delay_s())
    if source_type == "url":
        return fake_page_text(source)
    return await asyncio.to_thread(do_ingest, source, source_type, cfg)


def _job_response(record: dict) -> OnboardJobResponse:
    return OnboardJobResponse(**onboard_jobs.public(record))


def _start(cfg, ws: WorkspaceCtx, kind: str, key: str, work) -> OnboardJobResponse:
    record, created = onboard_jobs.create_job(cfg, str(ws.workspace_id), kind, key)
    if created:
        onboard_jobs.spawn(cfg, record, work)
    return _job_response(record)


async def _ingest(body: OnboardIngestRequest, ws: WorkspaceCtx, cfg) -> dict:
    """Ingest a URL/text and stage a ProfileDraft; the job's result.

    - ingest() is synchronous (httpx/filesystem) → run in thread
    - extract() is async → await directly
    - render() + stage() are synchronous → run in thread
    """
    import asyncio

    from agent.onboard import OnboardingExtractError, extract, render, slugify, stage
    from backend.services.onboard_fake import fake_extract

    with _onboarding_errors():
        raw_text = await _read_source(body.source, body.source_type, cfg)
        if fake_runs_enabled():
            draft = fake_extract(raw_text)
        else:
            draft = await extract(raw_text, cfg)
        try:
            slug = slugify(draft["company"]["name"])
        except ValueError as exc:
            raise OnboardingExtractError(f"extracted company name yields no slug: {exc}") from exc
        files = render(
            draft, template_knowledge_dir=cfg.repo_root / "profiles" / "_template" / "knowledge"
        )
        draft_id, staged_root = await asyncio.to_thread(
            stage, slug, files, cfg, draft["company"]["name"], draft=draft
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
    ).model_dump()


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=OnboardJobResponse)
@limiter.limit("10/minute")
async def ingest_endpoint(
    body: OnboardIngestRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> OnboardJobResponse:
    """Start ingesting a URL/text into a staged ProfileDraft. Poll ``GET /jobs/{job_id}``."""
    cfg = await _get_cfg(request, ws)
    key = onboard_jobs.source_key("ingest", body.source_type, body.source)
    return _start(cfg, ws, "ingest", key, lambda: _ingest(body, ws, cfg))


async def _re_extract(
    draft_id: str,
    product_slug: str,
    body: OnboardProductExtractRequest,
    ws: WorkspaceCtx,
    cfg,
    entry: dict,
) -> dict:
    """Re-extract one product and restage the draft under a new draft_id; the job's result."""
    import asyncio

    from agent.onboard import cancel, extract_product, render, stage
    from backend.services.onboard_fake import fake_extract_product

    with _onboarding_errors():
        extra_text = await _read_source(body.source, body.source_type, cfg)
        if fake_runs_enabled():
            updated_draft = fake_extract_product(product_slug, extra_text, entry["draft"])
        else:
            updated_draft = await extract_product(product_slug, extra_text, entry["draft"], cfg)

    slug = entry["slug"]
    cancel(entry["staged_root"])

    files = render(
        updated_draft, template_knowledge_dir=cfg.repo_root / "profiles" / "_template" / "knowledge"
    )
    new_draft_id, new_staged_root = await asyncio.to_thread(
        stage, slug, files, cfg, updated_draft["company"]["name"], draft=updated_draft
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
    ).model_dump()


@router.post(
    "/{draft_id}/product/{product_slug}/extract",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=OnboardJobResponse,
)
@limiter.limit("10/minute")
async def re_extract_product_endpoint(
    draft_id: UuidStr,
    product_slug: str,
    body: OnboardProductExtractRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> OnboardJobResponse:
    """Start re-extracting one product with an additional source URL/text. The draft must be
    the caller's (404 now, not in the job). Poll ``GET /jobs/{job_id}``; the result carries
    the new draft_id."""
    cfg = await _get_cfg(request, ws)
    entry = _get_owned_draft(draft_id, ws, cfg)
    key = onboard_jobs.source_key(
        "product_extract", draft_id, product_slug, body.source_type, body.source
    )
    return _start(
        cfg,
        ws,
        "product_extract",
        key,
        lambda: _re_extract(draft_id, product_slug, body, ws, cfg, entry),
    )


@router.get("/jobs/{job_id}", response_model=OnboardJobResponse)
async def get_job_endpoint(
    job_id: UuidStr,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> OnboardJobResponse:
    """An onboarding job's state. 404 for an unknown job or another workspace's."""
    cfg = await _get_cfg(request, ws)
    record = onboard_jobs.get_job(cfg, str(ws.workspace_id), job_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return _job_response(record)


@router.get("/{draft_id}/diff", response_model=OnboardDiffResponse)
async def get_diff_endpoint(
    draft_id: UuidStr,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> OnboardDiffResponse:
    """Return per-file diffs between staged files and the live profile."""
    import asyncio

    from agent.onboard import diff

    cfg = await _get_cfg(request, ws)
    entry = _get_owned_draft(draft_id, ws, cfg)

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
    """Promote the staged profile bundle to profiles/<slug>/ and register it as the
    workspace's profiles row (the default when the workspace has none yet)."""
    import asyncio

    from agent.onboard import DraftNotStagedError, ProfileAlreadyExistsError, promote

    cfg = await _get_cfg(request, ws)
    entry = _get_owned_draft(draft_id, ws, cfg)

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

    workspace_id = str(ws.workspace_id)
    try:
        # The profiles row and the rename share one transaction: a promote that raises
        # rolls the row back, so a failed promote never leaves an orphan registration.
        async with workspace_scope(request.app.state.pool, workspace_id) as conn:
            is_default = await register_promoted_profile(conn, workspace_id, slug)
            if is_default:
                await conn.execute(
                    """UPDATE workspaces
                       SET display_name = $1
                       WHERE id = $2::uuid AND display_name LIKE '%@%'""",
                    expected,
                    workspace_id,
                )
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

    return {
        "slug": slug,
        "profile_path": str(live_dir),
        "status": "promoted",
        "is_default": is_default,
    }


@router.delete("/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_endpoint(
    draft_id: UuidStr,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> None:
    """Cancel and clean up a staged onboarding draft."""
    from agent.onboard import cancel

    cfg = await _get_cfg(request, ws)
    entry = _get_owned_draft(draft_id, ws, cfg)
    _drafts.pop(draft_id, None)

    cancel(entry["staged_root"])
