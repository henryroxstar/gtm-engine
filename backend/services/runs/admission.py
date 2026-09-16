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
from .state import _MAX_CONCURRENT_RUNS_PER_WORKSPACE


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
    # paused and archived both refuse new runs (agent.schema.json lifecycle), and each says
    # which, so the app can offer "resume this agent" rather than "pick another".
    if agent_row["status"] == "paused":
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": "agent_paused"})
    if agent_row["status"] != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": "agent_archived"})
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


async def find_existing_run_id(pool, workspace_id: str, client_request_id: str) -> str | None:
    """RT-04: the run already created for this ``(workspace_id, client_request_id)`` pair,
    or None. Used both as ``create_run``'s fast-path replay check (before any admission
    check runs) and, inside :func:`insert_run_row`, as the race-loser's lookup after an
    ``ON CONFLICT DO NOTHING`` finds nothing to insert."""
    async with workspace_scope(pool, workspace_id) as conn:
        return await conn.fetchval(
            "SELECT id::text FROM runs WHERE workspace_id = $1::uuid AND client_request_id = $2",
            workspace_id,
            client_request_id,
        )


async def insert_run_row(
    pool,
    workspace_id: str,
    run_id: str,
    *,
    profile_name: str | None,
    agent_row: dict | None,
    body,
) -> tuple[str, bool, str | None, float | None, str | None]:
    """Write the run's `queued` row. Returns ``(effective_run_id, existed, agent_id,
    agent_budget_usd, language)`` — ``effective_run_id`` is ``run_id`` unless this call lost
    an idempotency race (see below), in which case it is the WINNING row's id; ``existed``
    is True exactly in that race-loser case, telling the caller to build its response from
    the existing row rather than from this call's own (unused) resolution.

    A5: the row is written ``queued`` with a ``payload``, and a worker's claim loop picks
    it up — the handler no longer spawns the work itself. The concurrency cap is enforced
    here too, in the same transaction as the INSERT (see :func:`_reserve_cap`), because it
    is now a claim about the whole deployment rather than about this process.

    Three resolutions ride along, all of them per-run and none cached:

    * **A4 attribution** — an agent-less run attaches to the workspace's default agent
      (lazily bootstrapped, race-safe). Best-effort: a failure here must never block the
      run, and the row then carries NULL exactly like every pre-A4 row.
    * **A4 budget** — the acting agent's cap, which can only narrow the workspace's.
    * **A7 language precedence** — request > agent > profile. Resolving to None means
      "unset", so the skills fall back to the profile's own setting and pre-A7 behaviour
      is untouched; the profile tier needs no lookup here.

    Raising leaves no row behind: the cap check and the INSERT share one transaction.

    **RT-04 idempotency (when ``body.client_request_id`` is set):** the INSERT below is an
    ``ON CONFLICT (workspace_id, client_request_id) WHERE client_request_id IS NOT NULL DO
    NOTHING`` — verified against the partial unique index (V025) directly, not assumed: the
    WHERE clause must repeat the index's own predicate for Postgres to accept it as a
    matching conflict target. This is race-safe, not merely a fast-path convenience:
    ``_reserve_cap``'s ``pg_advisory_xact_lock`` (below) is held for this whole transaction
    and keyed on ``workspace_id``, so two concurrent calls for the SAME workspace serialize —
    the second's INSERT never runs until the first's transaction has committed (or rolled
    back) and released the lock. By the time the second reaches its own INSERT, the first's
    row (if it won) is already durably visible, so ``ON CONFLICT DO NOTHING`` sees a REAL,
    committed conflict rather than racing an uncommitted one. When the INSERT returns no row,
    a plain SELECT on the SAME connection/transaction reads the winner's id.
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
    # The job record the worker rebuilds the run from. Pack mode used to be reconstructed
    # by REGEX from prompt_record above, a round trip that silently lost dry_run,
    # language, and agent_budget_usd — an edge case on the restart path that a queue
    # would have promoted to the only path. Everything the executor takes is here.
    payload = {
        "mode": "prompt" if body.pack is None else "pack",
        "pack": body.pack,
        "variant": body.variant,
        "inputs": dict(body.inputs or {}),
        "dry_run": bool(body.dry_run),
        "language": language,
        "agent_budget_usd": agent_budget_usd,
    }
    client_request_id = body.client_request_id
    async with workspace_scope(pool, workspace_id) as conn:
        await _reserve_cap(conn, workspace_id)
        inserted_id = await conn.fetchval(
            """INSERT INTO runs(id, workspace_id, profile_name, prompt, dry_run, status,
                                agent_id, payload, client_request_id)
               VALUES($1::uuid, $2::uuid, $3, $4, $5, 'queued', $6::uuid, $7::jsonb, $8)
               ON CONFLICT (workspace_id, client_request_id) WHERE client_request_id IS NOT NULL
                 DO NOTHING
               RETURNING id::text""",
            run_id,
            workspace_id,
            profile_name,
            prompt_record,
            body.dry_run,
            run_agent_id,
            json.dumps(payload),
            client_request_id,
        )
        if inserted_id is not None:
            return inserted_id, False, run_agent_id, agent_budget_usd, language
        # A conflict only fires when client_request_id is non-NULL (the partial index's own
        # predicate), and only means "this workspace already has a row for this id" — the
        # advisory lock above guarantees it is durably committed by now (see docstring), so
        # this SELECT cannot race an uncommitted insert.
        existing_id = await conn.fetchval(
            "SELECT id::text FROM runs WHERE workspace_id = $1::uuid AND client_request_id = $2",
            workspace_id,
            client_request_id,
        )
        if existing_id is None:
            # Structurally unreachable given the lock ordering above; fail loudly rather than
            # return a run_id nobody can look up if this invariant is ever violated.
            raise RuntimeError(
                f"client_request_id {client_request_id!r} conflicted but no row was found "
                f"for workspace {workspace_id!r}"
            )
        return existing_id, True, run_agent_id, agent_budget_usd, language


async def _reserve_cap(conn, workspace_id: str) -> None:
    """Refuse a 4th in-flight run for this workspace — 429, never a longer queue wait.

    The cap used to be a per-process set, which under N workers would have become an
    effective 3N: it is the mechanism that bounds a burst's cost blast radius and shrinks
    the budget-check TOCTOU window, so counting it globally is the point of moving it
    here. The set counted is exactly what the in-process set held (a gated run has always
    occupied a slot); only the SCOPE of the count changes.

    The advisory lock keeps the check exact under concurrency. Without it two admissions
    could both read 2 and both insert — read-committed gives each an unchanged snapshot
    of the other's uncommitted row. It is transaction-scoped, so it releases on commit or
    rollback with nothing to clean up, and it serialises admissions for ONE workspace.

    The key is the UUID's own first 64 bits, via the documented hex-bit-string cast, rather
    than ``hashtext()``: hashtext is an undocumented internal whose availability and hash
    algorithm are not contract, and the failure mode if it were ever missing is that every
    create raises — worth avoiding for an expression that needs no hash at all, since a
    workspace_id is already uniformly distributed.

    Over-cap stays an immediate 429 rather than a silent enqueue: the client already
    branches on 429, and turning a refusal into a wait would let a burst become expensive
    instead of being refused.
    """
    await conn.execute(
        "SELECT pg_advisory_xact_lock("
        "  ('x' || left(replace($1::uuid::text, '-', ''), 16))::bit(64)::bigint)",
        workspace_id,
    )
    in_flight = await conn.fetchval(
        """SELECT count(*) FROM runs
           WHERE workspace_id = $1::uuid
             AND status IN ('queued', 'running', 'awaiting_approval')""",
        workspace_id,
    )
    if (in_flight or 0) >= _MAX_CONCURRENT_RUNS_PER_WORKSPACE:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            {
                "code": "too_many_concurrent_runs",
                "message": f"Too many concurrent runs (max {_MAX_CONCURRENT_RUNS_PER_WORKSPACE})",
                "max": _MAX_CONCURRENT_RUNS_PER_WORKSPACE,
                "in_flight": in_flight,
            },
        )
