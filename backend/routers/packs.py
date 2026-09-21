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

from ..callers.limits import enforce_principal_rate
from ..callers.principal import Principal
from ..callers.rest import require_principal
from ..database import workspace_scope
from ..pack_catalog import (
    PackResolutionError,
    list_variants,
    readiness_block,
    resolve_variant,
    variant_readiness,
)
from ..schemas import ERROR_RESPONSES
from ..services.runs.principal_admission import audit_service_refusal_code
from ..types import UuidStr

router = APIRouter(prefix="/packs", tags=["packs"], responses=ERROR_RESPONSES)


async def _profile_for(
    principal: Principal, request: Request, profile_name: str | None
) -> str | None:
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
    async with workspace_scope(pool, principal.workspace_id) as conn:
        row = await conn.fetchrow(
            "SELECT profile_name FROM profiles WHERE workspace_id = $1::uuid AND is_default = true",
            principal.workspace_id,
        )
    return row["profile_name"] if row else None


async def _agent_view(principal: Principal, request: Request, agent_id: str):
    """An agent's (profile, pack subset). ``None`` subset = every pack the profile
    activates. 404 when the agent is not in this workspace."""
    from ..agents import fetch_agent

    async with workspace_scope(request.app.state.pool, principal.workspace_id) as conn:
        agent_row = await fetch_agent(conn, principal.workspace_id, agent_id)
    if agent_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown agent")
    packs = list(agent_row["packs"]) if agent_row["packs"] is not None else None
    return agent_row["profile_name"], packs


@router.get("")
async def list_packs(
    principal: Annotated[Principal, Depends(require_principal)],
    request: Request,
    _rate: Annotated[None, Depends(enforce_principal_rate)] = None,
    profile_name: str | None = None,
    agent_id: UuidStr | None = None,
) -> list[dict]:
    """Enumerate every variant this workspace's profile can run, as PackDescriptors.

    ``agent_id`` (A4) narrows the listing to that agent's view: its bound profile,
    intersected with its pack subset — a pack that fell out of the intersection
    simply disappears (inert, not an error).

    Fleet Phase A: a `kind="service"` principal is FORCED to its own bound
    `agent_id` — any caller-supplied `agent_id` is overridden, never merely
    checked, so a service principal can never see another agent's pack listing.
    A `kind="user"` principal is unchanged (today's `agent_id` param, byte-identical).
    """
    from ..pack_catalog import descriptor

    if principal.kind == "service":
        agent_id = principal.agent_id

    agent_packs: list[str] | None = None
    if agent_id is not None:
        profile_name, agent_packs = await _agent_view(principal, request, agent_id)

    profile = await _profile_for(principal, request, profile_name)
    if profile is None:
        return []
    repo_root = request.app.state.cfg.repo_root
    profiles_root = workspace_profiles_root(principal.workspace_id, repo_root)

    out: list[dict] = []
    profile_file = profiles_root / profile / "PROFILE.md"
    profile_text = profile_file.read_text(encoding="utf-8") if profile_file.is_file() else None
    for resolved in list_variants(repo_root, profiles_root, profile):
        if agent_packs is not None and resolved.graph.pack not in agent_packs:
            continue
        try:
            report = variant_readiness(profiles_root, profile, resolved)
        except ValueError:
            # Profile row exists but its files are not provisioned yet — the pack is
            # unrunnable for a knowable reason; say so rather than 500 or silence.
            report = None
        # principal.entitlement is the Entitlement enum in production but tests build
        # a frozen principal with a raw string — entitlement_meets accepts both.
        d = descriptor(
            resolved,
            entitlement=principal.entitlement,
            readiness=report,
            profile_text=profile_text,
        )
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
    principal: Annotated[Principal, Depends(require_principal)],
    request: Request,
    _rate: Annotated[None, Depends(enforce_principal_rate)] = None,
    profile_name: str | None = None,
) -> dict:
    """Full readiness checklist for one variant — every item, ready ones included.

    This is the onboarding wizard's feed: drive each blocked/degraded item green,
    and the variant unlocks. 404 unknown variant; 403 when the profile doesn't
    activate the pack (same information boundary as the listing's absence).

    Fleet Phase A: a `kind="service"` principal sees exactly what `list_packs` shows it
    — its agent's own profile (a caller-supplied `profile_name` is ignored), and a pack
    outside its agent's subset is the same 404 as a variant that does not exist.
    """
    if principal.kind == "service":
        profile_name, agent_packs = await _agent_view(principal, request, principal.agent_id)
        if agent_packs is not None and pack not in agent_packs:
            await audit_service_refusal_code(
                request.app.state.cfg, principal, request.url.path, "pack_not_found"
            )
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown pack variant")
    profile = await _profile_for(principal, request, profile_name)
    if profile is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            {"code": "profile_not_found", "message": "No profile for this workspace"},
        )
    repo_root = request.app.state.cfg.repo_root
    profiles_root = workspace_profiles_root(principal.workspace_id, repo_root)

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
