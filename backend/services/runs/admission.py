"""Admission checks for ``POST /v1/runs`` — everything that decides whether a run may
start, before any state is reserved or written.

Split out of the handler in Phase 1b so the router holds routing and the service package
holds decisions. Every refusal is an ``HTTPException`` with the same code and status the
handler raised before: these are the contract the mobile client branches on, and the
OpenAPI golden pins the surface.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from ...database import workspace_scope


async def resolve_acting_agent(pool, workspace_id: str, body) -> tuple[dict | None, str | None]:
    """A4: resolve the acting agent FIRST — it binds the profile everything else validates
    against. An explicit ``agent_id`` means full enforcement (profile binding, pack
    narrowing, budget, paused/archived refusal); absent, the workspace's default agent
    attaches later for attribution only, with no narrowing (pre-A4 clients are unaffected).

    Returns ``(agent_row, profile_name)``.
    """
    from ...agents import fetch_agent

    profile_name = body.profile_name
    if body.agent_id is None:
        _guard_profile_segment(profile_name)
        return None, profile_name

    async with workspace_scope(pool, workspace_id) as conn:
        agent_row = await fetch_agent(conn, workspace_id, body.agent_id)
    if agent_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown agent")
    if agent_row["status"] != "active":
        # paused and archived both refuse new runs (agent.schema.json lifecycle).
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": f"agent_{agent_row['status']}"})
    if body.profile_name and body.profile_name != agent_row["profile_name"]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"code": "agent_profile_mismatch", "agent_profile": agent_row["profile_name"]},
        )
    return agent_row, agent_row["profile_name"]


def _guard_profile_segment(profile_name: str | None) -> None:
    """B1 tenant boundary: a client-supplied ``profile_name`` is a path segment — it selects
    the profile directory every downstream join reads from and writes to. Reject any
    traversal shape here so it can never escape the workspace root (e.g.
    ``../../../../profiles/acme``, which would resolve onto the shared profiles mount). A
    bare name stays inside the caller's own workspace tree, so a shape check closes it.
    The agent path is exempt: it bound ``profile_name`` from a DB row.
    """
    from gtm_core.paths import _safe_segment

    try:
        _safe_segment(profile_name or "", "profile_name")
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, {"code": "invalid_profile_name"}
        ) from exc


async def resolve_pack_for_run(
    repo_root, workspace_id: str, entitlement, profile_name, agent_row, body
):
    """Resolve the requested pack variant and refuse the run unless it is activated,
    entitled, fully configured, and ready. Returns the resolved variant."""
    from gtm_core import gating
    from gtm_core.capabilities import entitlement_meets
    from gtm_core.paths import workspace_profiles_root

    from ...agents import agent_pack_allowed
    from ...pack_catalog import (
        PackResolutionError,
        blocked_items,
        missing_required_settings,
        resolve_variant,
        variant_readiness,
    )

    profiles_root = workspace_profiles_root(workspace_id, repo_root)
    try:
        resolved = resolve_variant(repo_root, profiles_root, profile_name, body.pack, body.variant)
    except PackResolutionError as exc:
        if exc.code == "unknown_variant":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown pack variant") from exc
        if exc.code == "pack_not_activated":
            raise HTTPException(status.HTTP_403_FORBIDDEN, {"code": "pack_not_activated"}) from exc
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"code": "pack_invalid"}) from exc

    # A4 narrowing at use: effective packs = agent.packs ∩ currently-activated (the
    # profile-activation side was just enforced by resolve_variant above).
    if agent_row is not None and not agent_pack_allowed(agent_row["packs"], body.pack):
        raise HTTPException(status.HTTP_403_FORBIDDEN, {"code": "agent_pack_not_allowed"})

    # Effective floor: `resolved.graph` is ALREADY the tenant-override merge
    # (resolve_variant -> merge_pack_override), so a tenant override that adds a higher-tier
    # node raises this check too — not just the base graph's own nodes. gtm_core/gating.toml,
    # never a hardcoded ladder — see gating.py.
    run_min_entitlement = gating.resolve_graph_entitlement(
        resolved.graph.pack,
        resolved.graph.variant,
        resolved.graph.nodes,
        explicit=resolved.graph.min_entitlement,
    )
    if not entitlement_meets(entitlement, run_min_entitlement):
        raise HTTPException(status.HTTP_403_FORBIDDEN, {"code": "entitlement_required"})

    missing = missing_required_settings(resolved, body.inputs)
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"code": "missing_settings", "missing": missing},
        )

    try:
        report = variant_readiness(profiles_root, profile_name, resolved)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {
                "code": "pack_not_ready",
                "blocked": [
                    {
                        "kind": "setting",
                        "name": "profile",
                        "reason": "profile files are not provisioned yet — run onboarding",
                    }
                ],
            },
        ) from exc
    blocked = blocked_items(report, resolved)
    if blocked:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, {"code": "pack_not_ready", "blocked": blocked}
        )
    return resolved


async def insert_run_row(
    pool,
    workspace_id: str,
    run_id: str,
    *,
    profile_name: str | None,
    agent_row: dict | None,
    body,
) -> tuple[str | None, float | None, str | None]:
    """Write the run's `pending` row and return ``(agent_id, agent_budget_usd, language)``.

    Three resolutions ride along, all of them per-run and none cached:

    * **A4 attribution** — an agent-less run attaches to the workspace's default agent
      (lazily bootstrapped, race-safe). Best-effort: a failure here must never block the
      run, and the row then carries NULL exactly like every pre-A4 row.
    * **A4 budget** — the acting agent's cap, which can only narrow the workspace's.
    * **A7 language precedence** — request > agent > profile. Resolving to None means
      "unset", so the skills fall back to the profile's own setting and pre-A7 behaviour
      is untouched; the profile tier needs no lookup here.

    The caller holds the concurrency slot; a failed INSERT raises, and releasing it is the
    caller's job (no task exists to run the done-callback on that path).
    """
    import json

    agent_budget_usd = (
        float(agent_row["monthly_budget_usd"])
        if agent_row is not None and agent_row["monthly_budget_usd"] is not None
        else None
    )
    language = body.language or (agent_row["language"] if agent_row is not None else None)

    run_agent_id: str | None = body.agent_id
    if run_agent_id is None:
        from ...agents import ensure_default_agent

        try:
            async with workspace_scope(pool, workspace_id) as conn:
                run_agent_id = await ensure_default_agent(conn, workspace_id)
        except Exception:  # noqa: BLE001
            run_agent_id = None

    # The runs.prompt column doubles as the audit line for pack mode (NOT NULL).
    prompt_record = (
        body.prompt
        if body.pack is None
        else f"[pack] {body.pack}/{body.variant} inputs={json.dumps(body.inputs, sort_keys=True)}"
    )
    async with workspace_scope(pool, workspace_id) as conn:
        await conn.execute(
            """INSERT INTO runs(id, workspace_id, profile_name, prompt, dry_run, status,
                                agent_id)
               VALUES($1::uuid, $2::uuid, $3, $4, $5, 'pending', $6::uuid)""",
            run_id,
            workspace_id,
            profile_name,
            prompt_record,
            body.dry_run,
            run_agent_id,
        )
    return run_agent_id, agent_budget_usd, language
