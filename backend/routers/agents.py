"""Agent CRUD under the workspace (Track A A4).

An agent is a named bundle of (profile, optional pack subset, optional budget,
optional language) — agent.schema.json is the wire contract. Narrowing-only is
enforced HERE at write time (superset packs / budget > cap are rejected) and
re-derived at every use in the runs router (both sides of the constraint move
after write, so the stored row is never trusted).

Lifecycle: DELETE archives — never hard-deletes — so per-agent ledger/history
attribution stays immutable. Archived agents refuse runs, drop out of default
listings, and resolve by id forever.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from gtm_core.paths import workspace_profiles_root

from ..agents import _AGENT_COLS, agent_row_to_dict, fetch_agent
from ..database import workspace_scope
from ..deps import WorkspaceCtx, require_auth
from ..schemas import (
    ERROR_RESPONSES,
    AgentCreateRequest,
    AgentResponse,
    AgentUpdateRequest,
)
from ..types import UuidStr

router = APIRouter(prefix="/agents", tags=["agents"], responses=ERROR_RESPONSES)


def _activated_packs(repo_root: Path, workspace_id: str, profile_name: str) -> frozenset[str]:
    """The profile's currently-activated pack names (empty when no packs.toml —
    fail-closed, matching pack-mode reachability)."""
    from gtm_core.packs.tenant import load_pack_activation

    path = workspace_profiles_root(workspace_id, repo_root) / profile_name / "packs.toml"
    if not path.exists():
        return frozenset()
    try:
        return frozenset(load_pack_activation(path).active)
    except Exception:  # noqa: BLE001 — malformed activation narrows to nothing
        return frozenset()


async def _validate_narrowing(
    conn,
    repo_root: Path,
    ws: WorkspaceCtx,
    *,
    profile_name: str,
    packs: list[str] | None,
    monthly_budget_usd: float | None,
) -> None:
    """The A4 write-time narrowing checks. Raises HTTPException on violation."""
    known = await conn.fetchval(
        "SELECT 1 FROM profiles WHERE workspace_id = $1::uuid AND profile_name = $2",
        ws.workspace_id,
        profile_name,
    )
    if not known:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"code": "unknown_profile", "profile_name": profile_name},
        )

    if packs is not None:
        activated = _activated_packs(repo_root, ws.workspace_id, profile_name)
        extra = sorted(set(packs) - activated)
        if extra:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                {"code": "packs_not_narrowing", "not_activated": extra},
            )

    if monthly_budget_usd is not None:
        cap = await conn.fetchval(
            "SELECT monthly_cost_cap_usd FROM subscriptions WHERE workspace_id = $1::uuid",
            ws.workspace_id,
        )
        if cap is not None and float(monthly_budget_usd) > float(cap):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                {"code": "budget_exceeds_cap", "cap_usd": float(cap)},
            )


@router.post(
    "",
    response_model=AgentResponse,
    response_model_exclude_none=True,  # contract optionality = absent, never null
    status_code=status.HTTP_201_CREATED,
)
async def create_agent(
    body: AgentCreateRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> AgentResponse:
    pool = request.app.state.pool
    repo_root = request.app.state.cfg.repo_root
    async with workspace_scope(pool, ws.workspace_id) as conn:
        await _validate_narrowing(
            conn,
            repo_root,
            ws,
            profile_name=body.profile_name,
            packs=body.packs,
            monthly_budget_usd=body.monthly_budget_usd,
        )
        taken = await conn.fetchval(
            """SELECT 1 FROM agents WHERE workspace_id = $1::uuid AND name = $2
               AND status <> 'archived'""",
            ws.workspace_id,
            body.name,
        )
        if taken:
            raise HTTPException(status.HTTP_409_CONFLICT, {"code": "agent_name_taken"})
        row = await conn.fetchrow(
            f"""INSERT INTO agents(workspace_id, name, profile_name, packs, language,
                                   monthly_budget_usd, read_scope, daily_dispatch_cap)
                VALUES($1::uuid, $2, $3, $4, $5, $6, $7, $8)
                RETURNING {_AGENT_COLS}""",  # noqa: S608 # nosec B608 — cols are a module constant
            ws.workspace_id,
            body.name,
            body.profile_name,
            body.packs,
            body.language,
            body.monthly_budget_usd,
            body.read_scope,
            body.daily_dispatch_cap,
        )
    return AgentResponse(**agent_row_to_dict(row))


@router.get("", response_model=list[AgentResponse], response_model_exclude_none=True)
async def list_agents(
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
    include_archived: bool = False,
) -> list[AgentResponse]:
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        if include_archived:
            rows = await conn.fetch(
                f"SELECT {_AGENT_COLS} FROM agents WHERE workspace_id = $1::uuid "  # noqa: S608 # nosec B608
                "ORDER BY created_at",
                ws.workspace_id,
            )
        else:
            rows = await conn.fetch(
                f"SELECT {_AGENT_COLS} FROM agents WHERE workspace_id = $1::uuid "  # noqa: S608 # nosec B608
                "AND status <> 'archived' ORDER BY created_at",
                ws.workspace_id,
            )
    return [AgentResponse(**agent_row_to_dict(r)) for r in rows]


@router.get("/{agent_id}", response_model=AgentResponse, response_model_exclude_none=True)
async def get_agent(
    agent_id: UuidStr,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> AgentResponse:
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        row = await fetch_agent(conn, ws.workspace_id, agent_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown agent")
    return AgentResponse(**agent_row_to_dict(row))


@router.patch("/{agent_id}", response_model=AgentResponse, response_model_exclude_none=True)
async def update_agent(
    agent_id: UuidStr,
    body: AgentUpdateRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> AgentResponse:
    pool = request.app.state.pool
    repo_root = request.app.state.cfg.repo_root
    fields = body.model_dump(exclude_unset=True)
    async with workspace_scope(pool, ws.workspace_id) as conn:
        current = await fetch_agent(conn, ws.workspace_id, agent_id)
        if current is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown agent")
        if current["status"] == "archived":
            raise HTTPException(status.HTTP_409_CONFLICT, {"code": "agent_archived"})

        # Absent key and present-but-None both skip their check (None = "all
        # activated" / "no budget" — always legal by construction).
        await _validate_narrowing(
            conn,
            repo_root,
            ws,
            profile_name=current["profile_name"],
            packs=fields.get("packs"),
            monthly_budget_usd=fields.get("monthly_budget_usd"),
        )
        if "name" in fields and fields["name"] != current["name"]:
            taken = await conn.fetchval(
                """SELECT 1 FROM agents WHERE workspace_id = $1::uuid AND name = $2
                   AND status <> 'archived' AND id <> $3::uuid""",
                ws.workspace_id,
                fields["name"],
                agent_id,
            )
            if taken:
                raise HTTPException(status.HTTP_409_CONFLICT, {"code": "agent_name_taken"})

        sets, args = [], []
        for col in (
            "name",
            "packs",
            "language",
            "monthly_budget_usd",
            "status",
            "read_scope",
            "daily_dispatch_cap",
        ):
            if col in fields:
                args.append(fields[col])
                sets.append(f"{col} = ${len(args)}")
        if not sets:
            return AgentResponse(**agent_row_to_dict(current))
        args.extend([agent_id, ws.workspace_id])
        row = await conn.fetchrow(
            f"""UPDATE agents SET {", ".join(sets)}
                WHERE id = ${len(args) - 1}::uuid AND workspace_id = ${len(args)}::uuid
                RETURNING {_AGENT_COLS}""",  # noqa: S608 # nosec B608 — cols from a fixed allowlist
            *args,
        )
    return AgentResponse(**agent_row_to_dict(row))


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_agent(
    agent_id: UuidStr,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> None:
    """DELETE = archive (idempotent). Ledger attribution stays immutable; the
    default flag is released so a fresh default can bootstrap."""
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        row = await fetch_agent(conn, ws.workspace_id, agent_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown agent")
        if row["status"] != "archived":
            await conn.execute(
                """UPDATE agents SET status = 'archived', is_default = false
                   WHERE id = $1::uuid AND workspace_id = $2::uuid""",
                agent_id,
                ws.workspace_id,
            )
