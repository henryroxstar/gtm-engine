"""Profile list and activation endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..database import workspace_scope
from ..deps import WorkspaceCtx, require_auth
from ..schemas import ActivateProfileRequest, ProfileResponse, ProfilesListResponse

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.get("", response_model=ProfilesListResponse)
async def list_profiles(
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> ProfilesListResponse:
    """List all profiles bound to this workspace."""
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        rows = await conn.fetch(
            """SELECT profile_name, is_default
               FROM profiles WHERE workspace_id = $1::uuid
               ORDER BY is_default DESC, profile_name""",
            ws.workspace_id,
        )

    profiles = [
        ProfileResponse(profile_name=r["profile_name"], is_default=r["is_default"]) for r in rows
    ]
    default = next((p.profile_name for p in profiles if p.is_default), None)
    active = default or (profiles[0].profile_name if profiles else "")

    return ProfilesListResponse(profiles=profiles, active=active)


@router.post("/{profile_name}/activate", response_model=ProfileResponse)
async def activate_profile(
    profile_name: str,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> ProfileResponse:
    """Set a profile as the default for this workspace."""
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM profiles WHERE workspace_id = $1::uuid AND profile_name = $2",
            ws.workspace_id,
            profile_name,
        )
        if not exists:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Profile {profile_name!r} not found")

        # Clear old default, set new one
        await conn.execute(
            "UPDATE profiles SET is_default = false WHERE workspace_id = $1::uuid",
            ws.workspace_id,
        )
        await conn.execute(
            """UPDATE profiles SET is_default = true
               WHERE workspace_id = $1::uuid AND profile_name = $2""",
            ws.workspace_id,
            profile_name,
        )

    return ProfileResponse(profile_name=profile_name, is_default=True)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ProfileResponse)
async def add_profile(
    body: ActivateProfileRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> ProfileResponse:
    """Register a new profile name for this workspace."""
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        await conn.execute(
            """INSERT INTO profiles(workspace_id, profile_name)
               VALUES($1::uuid, $2)
               ON CONFLICT (workspace_id, profile_name) DO NOTHING""",
            ws.workspace_id,
            body.profile_name,
        )
    return ProfileResponse(profile_name=body.profile_name, is_default=False)
