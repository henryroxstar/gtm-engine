"""Pack listing + readiness endpoints (A1/A12).

  GET /v1/packs                              → [PackDescriptor] for the workspace's profile
  GET /v1/packs/{pack}/{variant}/readiness   → full readiness checklist (wizard feed)

The listing is tenant-filtered by construction: a pack the profile doesn't activate
is ABSENT (not 403-on-run), and `available`/`locked_reason`/`readiness` are computed
server-side per requesting workspace so a client never orders entitlement tiers or
interprets provisioning state itself. Contract: schemas/pack-descriptor.schema.json
(the client repo mirrors it).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from gtm_core.paths import workspace_profiles_root

from ..database import workspace_scope
from ..deps import WorkspaceCtx, require_auth
from ..pack_catalog import (
    PackResolutionError,
    list_variants,
    readiness_block,
    resolve_variant,
    variant_readiness,
)

router = APIRouter(prefix="/packs", tags=["packs"])


async def _profile_for(ws: WorkspaceCtx, request: Request, profile_name: str | None) -> str | None:
    """The profile the listing is computed against: explicit param, else the
    workspace's default profile row. None ⇒ the workspace has no profile yet
    (empty catalog, not an error — onboarding hasn't run)."""
    if profile_name:
        # B1: the explicit profile_name is a path segment (it selects the profile
        # dir the catalog reads). Reject traversal shapes at the boundary.
        from gtm_core.paths import _safe_segment

        try:
            _safe_segment(profile_name, "profile_name")
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, {"code": "invalid_profile_name"}
            ) from exc
        return profile_name
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        row = await conn.fetchrow(
            "SELECT profile_name FROM profiles WHERE workspace_id = $1::uuid AND is_default = true",
            ws.workspace_id,
        )
    return row["profile_name"] if row else None


@router.get("")
async def list_packs(
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
    profile_name: str | None = None,
    agent_id: str | None = None,
) -> list[dict]:
    """Enumerate every variant this workspace's profile can run, as PackDescriptors.

    ``agent_id`` (A4) narrows the listing to that agent's view: its bound profile,
    intersected with its pack subset — a pack that fell out of the intersection
    simply disappears (inert, not an error).
    """
    from ..pack_catalog import descriptor

    agent_packs: list[str] | None = None
    if agent_id is not None:
        from ..agents import fetch_agent

        pool = request.app.state.pool
        async with workspace_scope(pool, ws.workspace_id) as conn:
            agent_row = await fetch_agent(conn, ws.workspace_id, agent_id)
        if agent_row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown agent")
        profile_name = agent_row["profile_name"]
        agent_packs = list(agent_row["packs"]) if agent_row["packs"] is not None else None

    profile = await _profile_for(ws, request, profile_name)
    if profile is None:
        return []
    repo_root = request.app.state.cfg.repo_root
    profiles_root = workspace_profiles_root(ws.workspace_id, repo_root)

    out: list[dict] = []
    for resolved in list_variants(repo_root, profiles_root, profile):
        if agent_packs is not None and resolved.graph.pack not in agent_packs:
            continue
        try:
            report = variant_readiness(profiles_root, profile, resolved)
        except ValueError:
            # Profile row exists but its files are not provisioned yet — the pack is
            # unrunnable for a knowable reason; say so rather than 500 or silence.
            report = None
        # ws.entitlement is the Entitlement enum in production but tests build the
        # frozen ctx with a raw string — entitlement_meets accepts both.
        d = descriptor(resolved, entitlement=ws.entitlement, readiness=report)
        if report is None:
            d["readiness"] = {
                "status": "blocked",
                "items": [
                    {
                        "kind": "setting",
                        "name": "profile",
                        "status": "blocked",
                        "reason": "profile files are not provisioned yet — run onboarding",
                    }
                ],
            }
        out.append(d)
    return out


@router.get("/{pack}/{variant}/readiness")
async def variant_readiness_detail(
    pack: str,
    variant: str,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
    profile_name: str | None = None,
) -> dict:
    """Full readiness checklist for one variant — every item, ready ones included.

    This is the onboarding wizard's feed: drive each blocked/degraded item green,
    and the variant unlocks. 404 unknown variant; 403 when the profile doesn't
    activate the pack (same information boundary as the listing's absence).
    """
    profile = await _profile_for(ws, request, profile_name)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No profile for this workspace")
    repo_root = request.app.state.cfg.repo_root
    profiles_root = workspace_profiles_root(ws.workspace_id, repo_root)

    try:
        resolved = resolve_variant(repo_root, profiles_root, profile, pack, variant)
    except PackResolutionError as exc:
        if exc.code == "unknown_variant":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown pack variant") from exc
        if exc.code == "pack_not_activated":
            raise HTTPException(status.HTTP_403_FORBIDDEN, {"code": "pack_not_activated"}) from exc
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"code": "pack_invalid"}) from exc

    try:
        report = variant_readiness(profiles_root, profile, resolved)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Profile files are not provisioned yet"
        ) from exc
    return {
        "pack": pack,
        "variant": variant,
        "profile_name": profile,
        "readiness": readiness_block(report, resolved, all_items=True),
    }
