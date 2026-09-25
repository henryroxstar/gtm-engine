"""Read paths for the run API: the SQL and the row→DTO shaping behind GET /runs,
GET /runs/{id}, and the artifact listing.

Split out of the router in Phase 1b. The handlers keep the HTTP decisions — 404 vs 410,
the response model, the download's headers — and the queries live here next to the writes
they mirror, so a schema change touches one package.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...database import workspace_scope
from .gates import _content_sha

_RUN_DETAIL_SQL = """SELECT r.id::text, r.status, r.profile_name, r.output, r.error, r.error_code,
          r.pending_gate, r.pending_content, r.agent_id::text AS agent_id, r.payload,
          r.created_at, r.principal_kind, r.principal_id, r.external_ref,
          (SELECT g.gate FROM run_gates g WHERE g.run_id = r.id AND g.state = 'open'
           ORDER BY g.opened_at DESC LIMIT 1) AS gate_kind,
          (SELECT g.node_id FROM run_gates g WHERE g.run_id = r.id AND g.state = 'open'
           ORDER BY g.opened_at DESC LIMIT 1) AS gate_node_id
   FROM runs r WHERE r.id = $1::uuid AND r.workspace_id = $2::uuid"""

_RUN_LIST_SQL = """SELECT id::text, status, profile_name, agent_id::text AS agent_id,
          payload, created_at, pending_gate, principal_kind, principal_id, external_ref,
          (SELECT g.gate FROM run_gates g WHERE g.run_id = runs.id AND g.state = 'open'
           ORDER BY g.opened_at DESC LIMIT 1) AS gate_kind,
          (SELECT g.node_id FROM run_gates g WHERE g.run_id = runs.id AND g.state = 'open'
           ORDER BY g.opened_at DESC LIMIT 1) AS gate_node_id
   FROM runs WHERE workspace_id = $1::uuid"""

_NODES_SQL = (
    "SELECT node_id, state, error FROM run_nodes "
    "WHERE run_id = $1::uuid AND workspace_id = $2::uuid ORDER BY node_id"
)
_BLOCKS_SQL = (
    "SELECT block FROM run_blocks WHERE run_id = $1::uuid AND workspace_id = $2::uuid ORDER BY ord"
)


def _blocks(rows) -> list[dict]:
    """A run_blocks row's `block` is jsonb; a fake conn may already hand back a dict."""
    return [b["block"] if isinstance(b["block"], dict) else json.loads(b["block"]) for b in rows]


def _wire_node(r) -> dict:
    """One nodes[] entry — for GET /v1/runs/{id} and the SSE snapshot alike: `{id, state}`,
    plus `error` (ST-09) only when the node failed and has one, as the `node` event does.
    run_nodes.error was persisted but never exposed before — a client could show the
    run-level error but not attribute it to a node."""
    node = {"id": r["node_id"], "state": r["state"]}
    error = r.get("error")
    if r["state"] == "failed" and error:
        node["error"] = error
    return node


def _payload(row) -> dict:
    """A run row's `payload` is jsonb (A5) and holds pack/variant/mode. Absent on
    pre-A5 rows and on fakes that don't model the column — treated as {}, so pack/
    variant surface as None rather than raising."""
    value = row.get("payload")
    if value is None:
        return {}
    return value if isinstance(value, dict) else json.loads(value)


def _isoformat(value) -> str | None:
    """`created_at` comes back as a datetime from asyncpg; a fake conn may already
    hand back a string or omit the column entirely."""
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else value


async def fetch_run_detail(pool, workspace_id: str, run_id: str) -> dict | None:
    """The run row plus its protocol-1 node and content rows, or None if not visible.

    One scope, three reads: a polling client must rebuild exactly the state a streaming
    client sees in the SSE snapshot, so the two must not read at different instants.
    """
    async with workspace_scope(pool, workspace_id) as conn:
        row = await conn.fetchrow(_RUN_DETAIL_SQL, run_id, workspace_id)
        node_rows = await conn.fetch(_NODES_SQL, run_id, workspace_id)
        block_rows = await conn.fetch(_BLOCKS_SQL, run_id, workspace_id)
    if row is None:
        return None
    payload = _payload(row)
    return {
        "run_id": row["id"],
        "status": row["status"],
        "profile_name": row["profile_name"],
        # .get: tolerate pre-A4 fixture rows without the column (asyncpg Record and dict
        # both support it).
        "agent_id": row.get("agent_id"),
        "pack": payload.get("pack"),
        "variant": payload.get("variant"),
        "inputs": payload.get("inputs"),
        "created_at": _isoformat(row.get("created_at")),
        "stages": [
            {
                "output": row["output"],
                "error": row["error"],
                "error_code": row.get("error_code"),
                "pending_gate": row["pending_gate"],
            }
        ],
        "error_code": row.get("error_code"),
        "pending_gate": row["pending_gate"],
        "pending_content": row["pending_content"],
        "pending_content_sha": _content_sha(row["pending_content"]),
        # The open gate's parsed kind + node id (client issue #240) — None once the gate is
        # decided or on a run that was never gated. .get: tolerate fakes/fixtures that model
        # only the pre-existing columns.
        "gate": row.get("gate_kind"),
        "pending_node_id": row.get("gate_node_id"),
        # None (not []) on runs with no persisted rows — prompt mode, and pre-A2 rows.
        "nodes": ([_wire_node(r) for r in node_rows] if node_rows else None),
        "content": _blocks(block_rows) if block_rows else None,
        # Fleet Phase A (V026): who started this run. .get: tolerate fakes/fixtures
        # that model only the pre-Fleet-Phase-A columns.
        "principal_kind": row.get("principal_kind"),
        "principal_id": row.get("principal_id"),
        "external_ref": row.get("external_ref"),
    }


async def fetch_recent_runs(
    pool,
    workspace_id: str,
    *,
    limit: int,
    agent_id: str | None,
    status: str | None = None,
    external_ref: str | None = None,
) -> list[dict]:
    """The workspace's most recent runs, newest first, optionally narrowed to one
    agent's (A4) and/or one status — e.g. status=awaiting_approval to find every run
    parked at a gate without paging through the full recent-runs list to find it.

    `limit` is bounded (``le=100``) at the router's Query validator, the only
    production caller — no internal re-cap here, so a caller that bypassed the
    router would no longer be silently truncated to 100."""
    sql = _RUN_LIST_SQL
    args: list = [workspace_id]
    if agent_id:
        args.append(agent_id)
        sql += f" AND agent_id = ${len(args)}::uuid"
    if status:
        args.append(status)
        sql += f" AND status = ${len(args)}"
    if external_ref:
        args.append(external_ref)
        sql += f" AND external_ref = ${len(args)}"
    args.append(limit)
    sql += f" ORDER BY created_at DESC LIMIT ${len(args)}"
    async with workspace_scope(pool, workspace_id) as conn:
        rows = await conn.fetch(sql, *args)
    return [
        {
            "run_id": r["id"],
            "status": r["status"],
            "profile_name": r["profile_name"],
            "agent_id": r.get("agent_id"),
            "pack": _payload(r).get("pack"),
            "variant": _payload(r).get("variant"),
            "inputs": _payload(r).get("inputs"),
            "created_at": _isoformat(r.get("created_at")),
            "pending_gate": r.get("pending_gate"),
            # The open gate's parsed kind + node id (same fields _RUN_DETAIL_SQL
            # exposes) — None once the gate is decided or on a run never gated.
            "gate": r.get("gate_kind"),
            "pending_node_id": r.get("gate_node_id"),
            "principal_kind": r.get("principal_kind"),
            "principal_id": r.get("principal_id"),
            "external_ref": r.get("external_ref"),
        }
        for r in rows
    ]


async def fetch_run_agent_id(pool, workspace_id: str, run_id: str) -> str | None:
    """The run's bound ``agent_id`` alone — for a route that needs to scope a service
    principal's read (Fleet Phase A) but doesn't already have the run's full detail in
    hand (cancel/stream/artifacts read their own narrower row shape). Returns None for
    both "not found" and "found with no bound agent" — callers that need to
    distinguish those already checked existence first."""
    async with workspace_scope(pool, workspace_id) as conn:
        row = await conn.fetchrow(
            "SELECT agent_id::text AS agent_id FROM runs "
            "WHERE id = $1::uuid AND workspace_id = $2::uuid",
            run_id,
            workspace_id,
        )
    return row["agent_id"] if row else None


async def fetch_run_artifacts(pool, workspace_id: str, run_id: str) -> list[dict] | None:
    """The run's registered file deliverables (schemas/run-artifact.schema.json), or None
    when the run is not visible to this workspace — an unknown and a cross-tenant run are
    indistinguishable on the wire, so the listing is no existence oracle."""
    async with workspace_scope(pool, workspace_id) as conn:
        run_row = await conn.fetchrow(
            "SELECT 1 FROM runs WHERE id = $1::uuid AND workspace_id = $2::uuid",
            run_id,
            workspace_id,
        )
        rows = await conn.fetch(
            """SELECT id::text, run_id::text, rel_path, name, size_bytes, media_type,
                      sha256, node_id, created_at
               FROM run_artifacts
               WHERE run_id = $1::uuid AND workspace_id = $2::uuid
               ORDER BY created_at, rel_path""",
            run_id,
            workspace_id,
        )
    if run_row is None:
        return None
    artifacts = []
    for r in rows:
        entry = {
            "artifact_id": r["id"],
            "run_id": r["run_id"],
            "name": r["name"],
            "rel_path": r["rel_path"],
            "size_bytes": r["size_bytes"],
            "media_type": r["media_type"],
            "sha256": r["sha256"],
            "created_at": r["created_at"].isoformat(),
        }
        if r["node_id"]:
            entry["node_id"] = r["node_id"]
        artifacts.append(entry)
    return artifacts


async def fetch_artifact(pool, workspace_id: str, run_id: str, artifact_id: str):
    """One artifact's pointer row (rel_path/name/media_type), or None."""
    async with workspace_scope(pool, workspace_id) as conn:
        return await conn.fetchrow(
            """SELECT rel_path, name, media_type FROM run_artifacts
               WHERE id = $1::uuid AND run_id = $2::uuid AND workspace_id = $3::uuid""",
            artifact_id,
            run_id,
            workspace_id,
        )


def resolve_artifact_path(content_root, rel_path: str):
    """Resolve an artifact row's pointer to a real file inside the workspace root.

    Returns ``(path, reason)``: a path when the bytes are servable, else ``None`` with
    ``"gone"`` (registered but deleted — account edit, retention; the client should drop
    the stale reference, hence 410) or ``"escaped"`` (the row resolves outside the root —
    tampered or symlinked; refused loudly in logs, 404 on the wire so it is no oracle).
    """
    from ...artifacts import resolve_contained

    path = resolve_contained(content_root, rel_path)
    if path is not None:
        return path, None
    return None, ("escaped" if (content_root / rel_path).exists() else "gone")


def attachment_headers(name: str) -> dict[str, str]:
    """RFC 5987 ``filename*`` with an ASCII fallback; attachment-always + nosniff, so
    agent-produced HTML is never rendered inline in a browser origin."""
    from urllib.parse import quote

    ascii_name = name.encode("ascii", "replace").decode()
    return {
        "Content-Disposition": (
            f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"
        ),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }


def resolve_artifact_response(row: Any, ws: str, repo_root: Path, artifact_id: str | None = None):
    """Serve an artifact row, redirecting to presigned Cloudflare R2 URL when configured,
    or falling back to local FileResponse."""
    from gtm_core import r2_client

    if r2_client.is_configured():
        key = f"{ws}/content/{row['rel_path']}"
        try:
            url = r2_client.generate_presigned_url(key)
            from fastapi.responses import RedirectResponse

            return RedirectResponse(url, status_code=307)
        except Exception:  # noqa: BLE001 # nosec B110
            pass

    import logging

    from fastapi import HTTPException, status
    from fastapi.responses import FileResponse

    from gtm_core.paths import workspace_content_root

    content_root = workspace_content_root(ws, repo_root)
    path, reason = resolve_artifact_path(content_root, row["rel_path"])
    if path is None:
        if reason == "escaped":
            aid = artifact_id or (row["id"] if "id" in row else "unknown")
            logging.getLogger(__name__).warning(
                "artifact %s resolution escaped the workspace root — refused", aid
            )
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")
        raise HTTPException(
            status.HTTP_410_GONE,
            {"code": "artifact_gone", "message": "Artifact is no longer available"},
        )

    return FileResponse(path, media_type=row["media_type"], headers=attachment_headers(row["name"]))
