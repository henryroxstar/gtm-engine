"""Profile knowledge staging, diffing, promotion, and case study router (PRD-324)."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from gtm_core import knowledge_meta as km
from gtm_core.knowledge_staging import (
    _safe_topic_relpath,
    diff,
    list_staged,
    live_path,
    promote,
    stage,
    staged_path,
)
from gtm_core.ledgers import Ledgers
from gtm_core.paths import workspace_content_root, workspace_profiles_root

from ..database import workspace_scope
from ..deps import WorkspaceCtx, require_auth
from ..ratelimit import limiter
from ..schemas import (
    ERROR_RESPONSES,
    CaseStudyResponse,
    CaseStudyStageRequest,
    KnowledgeDiffResponse,
    KnowledgePromoteRequest,
    KnowledgePromoteResponse,
    KnowledgeStageRequest,
    KnowledgeTopicResponse,
    StagedTopicsResponse,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/profiles", tags=["profile_knowledge"], responses=ERROR_RESPONSES)


def _validate_topic(topic: str) -> Path:
    """Validate topic path against traversal, NUL, and unsupported extensions."""
    parts = Path(topic).parts
    if (
        any(s in ("", ".", "..") or "\x00" in s or s.startswith("/") for s in parts)
        or Path(topic).is_absolute()
        or ".." in topic
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unsafe topic: {topic!r}")
    try:
        return _safe_topic_relpath(topic)
    except ValueError as exc:
        if "unsupported topic extension" in str(exc):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                {"code": "unsupported_topic_extension", "message": str(exc)},
            ) from exc
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {"code": "invalid_topic", "message": str(exc)},
        ) from exc


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


def _get_roots(request: Request, ws: WorkspaceCtx) -> tuple[Path, Path]:
    repo = getattr(getattr(request.app.state, "cfg", None), "repo_root", None)
    return workspace_profiles_root(ws.workspace_id, repo), workspace_content_root(
        ws.workspace_id, repo
    )


@router.get("/{profile_name}/knowledge-staging", response_model=StagedTopicsResponse)
@limiter.limit("30/minute")
async def get_staged_topics(
    profile_name: str,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> StagedTopicsResponse:
    """List pending staged topics carrying unapproved candidate changes."""
    await _require_profile(request, ws, profile_name)
    _, content_root = _get_roots(request, ws)
    return StagedTopicsResponse(staged_topics=list_staged(content_root, profile_name))


def _log_promoted(content_root: Path, profile_name: str, topic: str) -> None:
    try:
        Ledgers(SimpleNamespace(content_root=content_root), profile_name).append_history(
            {"event": "knowledge_promoted", "topic": topic}
        )
    except Exception:
        log.exception("Failed to append audit history for %s/%s", profile_name, topic)


@router.post("/{profile_name}/case-studies/stage", response_model=CaseStudyResponse)
@limiter.limit("30/minute")
async def stage_case_study(
    profile_name: str,
    body: CaseStudyStageRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> CaseStudyResponse:
    """Merge structured case study into candidate staging, with optional direct promote."""
    await _require_profile(request, ws, profile_name)
    profiles_root, content_root = _get_roots(request, ws)

    staged_cs = staged_path(content_root, profile_name, "case-studies.md")
    live_cs = live_path(profiles_root, profile_name, "case-studies.md")

    if body.auto_promote and staged_cs.is_file():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "staged_candidate_exists",
                "message": "Cannot auto_promote while unreviewed staged candidate exists for case-studies.md",
            },
        )

    base_text = (
        staged_cs.read_text(encoding="utf-8")
        if staged_cs.is_file()
        else (
            live_cs.read_text(encoding="utf-8")
            if live_cs.is_file()
            else f"---\nsource: manual\nrefreshed: {date.today().isoformat()}\nreview: 90d\n---\n# Case Studies\n"
        )
    )

    nums = [int(m) for m in re.findall(r"(?im)^##\s+case\s+study\s+(\d+)", base_text)]
    next_n = max(nums) + 1 if nums else len(re.findall(r"(?im)^##\s+case\s+study", base_text)) + 1
    block = (
        f"\n## Case study {next_n}\n- **Customer:** {body.customer}\n"
        f"- **Problem:** {body.problem}\n- **What you did:** {body.action}\n- **Result:** {body.result}\n"
    )
    new_text = (base_text if base_text.endswith("\n") else base_text + "\n") + block
    stage(content_root, profile_name, "case-studies.md", new_text)
    diff_str = diff(profiles_root, content_root, profile_name, "case-studies.md")

    if body.auto_promote:
        today = date.today()
        promote(
            profiles_root,
            content_root,
            profile_name,
            "case-studies.md",
            today=today,
            source="client-api",
        )
        _log_promoted(content_root, profile_name, "case-studies.md")

    return CaseStudyResponse(
        customer=body.customer,
        problem=body.problem,
        action=body.action,
        result=body.result,
        staged=not body.auto_promote,
        promoted=body.auto_promote,
        diff=diff_str,
    )


@router.post("/{profile_name}/knowledge/{topic:path}/stage")
@limiter.limit("30/minute")
async def stage_knowledge_topic(
    profile_name: str,
    topic: str,
    body: KnowledgeStageRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> dict[str, str]:
    """Stage a candidate update for a knowledge topic."""
    _validate_topic(topic)
    await _require_profile(request, ws, profile_name)
    profiles_root, content_root = _get_roots(request, ws)

    content_to_stage = body.content
    if not content_to_stage.startswith("---") and (
        topic.endswith(".md") or "." not in Path(topic).name
    ):
        live = live_path(profiles_root, profile_name, topic)
        live_fm = {}
        if live.is_file():
            live_fm, _ = km.parse_frontmatter(live.read_text(encoding="utf-8"))
        if body.frontmatter:
            live_fm.update(body.frontmatter)
        if live_fm:
            content_to_stage = km.upsert_frontmatter(content_to_stage, live_fm)

    stage(content_root, profile_name, topic, content_to_stage)
    return {"status": "staged", "topic": topic}


@router.delete("/{profile_name}/knowledge/{topic:path}/stage")
@limiter.limit("30/minute")
async def discard_staged_topic(
    profile_name: str,
    topic: str,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> dict[str, str]:
    """Discard an unpromoted candidate update for a knowledge topic."""
    _validate_topic(topic)
    await _require_profile(request, ws, profile_name)
    _, content_root = _get_roots(request, ws)
    staged = staged_path(content_root, profile_name, topic)
    if not staged.is_file():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No staged candidate for {profile_name}/{topic}"
        )
    staged.unlink(missing_ok=True)
    return {"status": "discarded", "topic": topic}


@router.get("/{profile_name}/knowledge/{topic:path}/diff", response_model=KnowledgeDiffResponse)
@limiter.limit("30/minute")
async def diff_knowledge_topic(
    profile_name: str,
    topic: str,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> KnowledgeDiffResponse:
    """Unified diff between live file and staged candidate."""
    _validate_topic(topic)
    await _require_profile(request, ws, profile_name)
    profiles_root, content_root = _get_roots(request, ws)

    staged = staged_path(content_root, profile_name, topic)
    live = live_path(profiles_root, profile_name, topic)
    if not staged.is_file() and not live.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Topic {topic!r} not found")
    if not staged.is_file():
        return KnowledgeDiffResponse(
            topic=topic, diff="", has_changes=False, staged=False, candidate_sha=None
        )

    diff_str = diff(profiles_root, content_root, profile_name, topic)
    candidate_sha = hashlib.sha256(staged.read_bytes()).hexdigest()
    return KnowledgeDiffResponse(
        topic=topic,
        diff=diff_str,
        has_changes=bool(diff_str.strip()),
        staged=True,
        candidate_sha=candidate_sha,
    )


@router.post(
    "/{profile_name}/knowledge/{topic:path}/promote", response_model=KnowledgePromoteResponse
)
@limiter.limit("30/minute")
async def promote_knowledge_topic(
    profile_name: str,
    topic: str,
    body: KnowledgePromoteRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> KnowledgePromoteResponse:
    """Promote a staged candidate topic to live profile truth."""
    _validate_topic(topic)
    await _require_profile(request, ws, profile_name)
    profiles_root, content_root = _get_roots(request, ws)

    staged = staged_path(content_root, profile_name, topic)
    if not staged.is_file():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No staged candidate for {profile_name}/{topic}"
        )

    actual_sha = hashlib.sha256(staged.read_bytes()).hexdigest()
    if actual_sha != body.candidate_sha:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "content_sha_mismatch", "message": "content_sha_mismatch"},
        )

    today = date.today()
    try:
        promote(profiles_root, content_root, profile_name, topic, today=today, source="client-api")
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    _log_promoted(content_root, profile_name, topic)
    return KnowledgePromoteResponse(topic=topic, status="promoted", refreshed=today.isoformat())


@router.get("/{profile_name}/knowledge/{topic:path}", response_model=KnowledgeTopicResponse)
@limiter.limit("30/minute")
async def get_knowledge_topic(
    profile_name: str,
    topic: str,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> KnowledgeTopicResponse:
    """Retrieve live knowledge topic content and extracted frontmatter."""
    _validate_topic(topic)
    await _require_profile(request, ws, profile_name)
    profiles_root, _ = _get_roots(request, ws)

    target = live_path(profiles_root, profile_name, topic)
    if not target.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Knowledge topic {topic!r} not found")

    text = target.read_text(encoding="utf-8")
    if target.suffix == ".md":
        fm, body = km.parse_frontmatter(text)
        return KnowledgeTopicResponse(topic=topic, content=body, frontmatter=fm or None)
    return KnowledgeTopicResponse(topic=topic, content=text, frontmatter=None)
