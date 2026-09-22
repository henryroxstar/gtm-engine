"""Headless pack dispatcher (PRD 2026-09-20 / §4.1).

Dispatches automated headless pipelines (e.g. systemd/cron timers) directly into
the Postgres-backed run queue, bypassing the public REST API and edge gateway to
avoid circular auth dependencies.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from typing import Any

from backend.database import workspace_scope
from gtm_core.paths import clean_env_var

log = logging.getLogger("backend.dispatch_headless")


async def dispatch_headless_run(
    pool: Any,
    workspace_id: str,
    profile_name: str,
    pack: str = "headless-content",
    variant: str = "content_pipeline",
    *,
    dry_run: bool = False,
    inputs: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> str:
    """Insert a queued run row directly into PostgreSQL within workspace RLS scope."""
    run_id = uuid.uuid4()
    ws_uuid = uuid.UUID(str(workspace_id))
    prompt = f"[pack] {pack}/{variant} inputs={json.dumps(inputs or {})}"
    payload = {
        "mode": "pack",
        "pack": pack,
        "variant": variant,
        "dry_run": dry_run,
        "inputs": inputs or {},
        "context": context or {},
    }

    async with workspace_scope(pool, str(ws_uuid)) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO runs (id, workspace_id, profile_name, prompt, status, payload)
            VALUES ($1::uuid, $2::uuid, $3, $4, 'queued', $5::jsonb)
            RETURNING id
            """,
            run_id,
            ws_uuid,
            profile_name,
            prompt,
            json.dumps(payload),
        )
        return str(row["id"])


async def _resolve_workspace_id(pool: Any, explicit_id: str | None = None) -> str:
    """Resolve target workspace ID or default to the first workspace in DB."""
    if explicit_id:
        return explicit_id

    env_ws = clean_env_var("WORKSPACE_ID") or clean_env_var("GTM_WORKSPACE_ID")
    if env_ws:
        return env_ws

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1")
        if not row:
            raise RuntimeError("No workspaces found in database. Create a workspace first.")
        return str(row["id"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dispatch headless pipeline packs directly to run queue"
    )
    parser.add_argument(
        "--pack", default="headless-content", help="Pack name (default: headless-content)"
    )
    parser.add_argument(
        "--variant", default="content_pipeline", help="Pack variant (default: content_pipeline)"
    )
    parser.add_argument("--profile", default=None, help="Active profile name")
    parser.add_argument("--workspace-id", default=None, help="Target workspace UUID")
    parser.add_argument("--dry-run", action="store_true", help="Execute in dry-run mode")
    parser.add_argument("--inputs", default="{}", help="JSON-encoded input dictionary")
    parser.add_argument("--context", default="{}", help="JSON-encoded context dictionary")
    return parser


async def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    profile = (
        args.profile or clean_env_var("ACTIVE_PROFILE") or clean_env_var("GTM_PROFILE") or "default"
    )

    try:
        inputs = json.loads(args.inputs)
        context = json.loads(args.context)
    except json.JSONDecodeError as exc:
        print(f"Error parsing JSON arguments: {exc}", file=sys.stderr)
        return 2

    from backend.database import create_pool

    db_url = clean_env_var("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL environment variable is required.", file=sys.stderr)
        return 1

    pool = await create_pool(db_url)
    try:
        workspace_id = await _resolve_workspace_id(pool, args.workspace_id)
        run_id = await dispatch_headless_run(
            pool=pool,
            workspace_id=workspace_id,
            profile_name=profile,
            pack=args.pack,
            variant=args.variant,
            dry_run=args.dry_run,
            inputs=inputs,
            context=context,
        )
        print(
            json.dumps(
                {
                    "status": "queued",
                    "run_id": run_id,
                    "workspace_id": workspace_id,
                    "profile": profile,
                    "pack": args.pack,
                    "variant": args.variant,
                    "dry_run": args.dry_run,
                }
            )
        )
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
