"""Account self-service endpoints.

PATCH  /v1/account         — update display_name and/or password
DELETE /v1/account         — hard-delete the authenticated user (Apple App Store requirement)
GET    /v1/account/export  — GDPR Art. 20 data export (all workspace data as JSON)

Both mutation endpoints require authentication. Password change additionally requires
current_password to prevent account takeover via an unattended session.

DELETE cascades via Postgres FK: users → workspaces → subscriptions / profiles /
runs / push_tokens / api_keys / cost_records.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from gtm_core.paths import workspace_content_root, workspace_tree

from .. import auth as _auth
from ..database import workspace_scope
from ..deps import WorkspaceCtx, require_auth
from ..schemas import DeleteAccountRequest, PatchAccountRequest

router = APIRouter(prefix="/account", tags=["account"])


@router.patch("", status_code=status.HTTP_200_OK)
async def patch_account(
    body: PatchAccountRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> dict:
    """Update display_name and/or password for the authenticated user."""
    pool = request.app.state.pool

    if body.display_name is None and body.new_password is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Provide at least one of display_name or new_password",
        )

    if body.new_password is not None:
        if body.current_password is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "current_password is required to change password",
            )
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT password_hash FROM users WHERE id = $1::uuid", ws.user_id
            )
        if row is None or not _auth.verify_password(body.current_password, row["password_hash"]):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is incorrect")

        new_hash = _auth.hash_password(body.new_password)
        async with pool.acquire() as conn:
            # Stamp password_changed_at so refresh tokens issued before now are
            # rejected (VULN-0003) — a password change must lock out stolen tokens.
            await conn.execute(
                "UPDATE users SET password_hash = $1, password_changed_at = now() "
                "WHERE id = $2::uuid",
                new_hash,
                ws.user_id,
            )

    if body.display_name is not None:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET display_name = $1 WHERE id = $2::uuid",
                body.display_name,
                ws.user_id,
            )

    return {"status": "updated"}


@router.get("/export", status_code=status.HTTP_200_OK)
async def export_account(
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> dict:
    """Return a full JSON export of all data held for the authenticated workspace.

    Covers GDPR Art. 20 (data portability). The export includes the user record
    (minus password hash), workspace, subscription, profiles, runs, and API keys.
    Push tokens and cost records are intentionally excluded — tokens are ephemeral
    device state; cost records are billing records held for the operator's own ledger.
    """
    pool = request.app.state.pool

    async with pool.acquire() as conn:
        user_row = await conn.fetchrow(
            "SELECT id::text, email, display_name, created_at, last_seen_at "
            "FROM users WHERE id = $1::uuid",
            ws.user_id,
        )

    async with workspace_scope(pool, ws.workspace_id) as conn:
        ws_row = await conn.fetchrow(
            "SELECT id::text, slug, display_name, created_at FROM workspaces WHERE id = $1::uuid",
            ws.workspace_id,
        )
        sub_row = await conn.fetchrow(
            "SELECT entitlement, status, monthly_cost_cap_usd, created_at "
            "FROM subscriptions WHERE workspace_id = $1::uuid",
            ws.workspace_id,
        )
        profiles = await conn.fetch(
            "SELECT profile_name, is_default, created_at FROM profiles "
            "WHERE workspace_id = $1::uuid ORDER BY created_at",
            ws.workspace_id,
        )
        runs = await conn.fetch(
            "SELECT id::text, profile_name, prompt, status, created_at, completed_at "
            "FROM runs WHERE workspace_id = $1::uuid ORDER BY created_at DESC LIMIT 500",
            ws.workspace_id,
        )
        api_keys = await conn.fetch(
            "SELECT id::text, prefix, label, entitlement, created_at, revoked_at "
            "FROM api_keys WHERE workspace_id = $1::uuid ORDER BY created_at",
            ws.workspace_id,
        )

    def _iso(v):
        return v.isoformat() if v is not None else None

    # On-disk account deliverables (dossiers, prospect lists, outreach drafts) —
    # enumerated so the export ACKNOWLEDGES this PII (GDPR Art. 20). File bodies are
    # fetched via the per-account paths, not inlined into this JSON.
    accounts_dir = (
        workspace_content_root(ws.workspace_id, request.app.state.cfg.repo_root) / "accounts"
    )
    account_files: list[dict] = []
    if accounts_dir.is_dir():
        for p in sorted(accounts_dir.rglob("*")):
            # A broken symlink or an unreadable file must not 500 the entire GDPR
            # export — skip what cannot be stat'd rather than fail the endpoint.
            try:
                if p.is_file():
                    account_files.append(
                        {"path": str(p.relative_to(accounts_dir)), "size_bytes": p.stat().st_size}
                    )
            except OSError:
                continue

    return {
        "user": {
            "id": user_row["id"],
            "email": user_row["email"],
            "display_name": user_row["display_name"],
            "created_at": _iso(user_row["created_at"]),
            "last_seen_at": _iso(user_row["last_seen_at"]),
        },
        "workspace": {
            "id": ws_row["id"],
            "slug": ws_row["slug"],
            "display_name": ws_row["display_name"],
            "created_at": _iso(ws_row["created_at"]),
        },
        "subscription": {
            "entitlement": sub_row["entitlement"],
            "status": sub_row["status"],
            "monthly_cost_cap_usd": float(sub_row["monthly_cost_cap_usd"]),
            "created_at": _iso(sub_row["created_at"]),
        }
        if sub_row
        else None,
        "profiles": [
            {
                "profile_name": r["profile_name"],
                "is_default": r["is_default"],
                "created_at": _iso(r["created_at"]),
            }
            for r in profiles
        ],
        "runs": [
            {
                "id": r["id"],
                "profile_name": r["profile_name"],
                "prompt": r["prompt"],
                "status": r["status"],
                "created_at": _iso(r["created_at"]),
                "completed_at": _iso(r["completed_at"]),
            }
            for r in runs
        ],
        "api_keys": [
            {
                "id": r["id"],
                "prefix": r["prefix"],
                "label": r["label"],
                "entitlement": r["entitlement"],
                "created_at": _iso(r["created_at"]),
                "is_revoked": r["revoked_at"] is not None,
            }
            for r in api_keys
        ],
        "account_files": account_files,
    }


@router.delete("", status_code=status.HTTP_200_OK)
async def delete_account(
    body: DeleteAccountRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> dict:
    """Hard-delete the authenticated user and ALL their data — DB **and** on-disk.

    Requires the current password (step-up re-auth) for an irreversible action.
    Postgres CASCADE propagates the DB rows (workspace → subscriptions, profiles,
    runs, push_tokens, api_keys, cost_records); the workspace's on-disk tree
    (``data/workspaces/<ws>/`` — account dossiers, prospect PII, outreach) is
    removed too. GDPR Art. 17: fail loud, so an erasure that cannot complete is
    never silently reported as done.
    """
    pool = request.app.state.pool

    # Step-up re-auth — an unattended session must not be able to nuke the account.
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT password_hash FROM users WHERE id = $1::uuid", ws.user_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if not _auth.verify_password(body.current_password, row["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is incorrect")

    # 1. Remove on-disk PII FIRST: a filesystem failure then leaves the DB intact
    #    (caller can retry) instead of orphaning files after the rows are gone.
    tree = workspace_tree(ws.workspace_id, request.app.state.cfg.repo_root)
    if tree.exists():
        # rmtree is blocking and potentially large — off-load it so a big tree
        # cannot stall the event loop. Still raises → 500 on failure, so PII is
        # never silently stranded (GDPR Art. 17: an erasure that cannot complete
        # must not be reported as done).
        await asyncio.to_thread(shutil.rmtree, tree)

    # 2. Cascade-delete the DB rows.
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM users WHERE id = $1::uuid", ws.user_id)
    if result == "DELETE 0":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return {"status": "deleted"}
