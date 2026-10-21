"""Fleet Phase A (Task 3) — composition glue between ``backend/callers/`` (Task 3's
own identity/admission primitives: Principal, require_principal, bind_agent,
require_pack_mode, authorize_run_read, enforce_agent_daily_cap) and the runs/packs
routers.

Kept as its own module rather than inlined in ``backend/routers/runs.py`` — that file
is at/near this repo's §R10 file-size ceiling, so each route calls ONE or TWO
functions from here instead of composing the bind_agent/require_pack_mode/
enforce_agent_daily_cap/authorize_run_read sequence per route.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from ...callers import audit
from ...callers.dependency import authorize_run_read, bind_agent, require_pack_mode
from ...callers.ports import Refusal
from ...callers.principal import Principal
from ...database import workspace_scope
from ...session import _workspace_scoped_config


def admit_run_creation(principal: Principal, body) -> None:
    """The structural (DB-free) half of POST /v1/runs' admission sequence — safe to run
    BEFORE resolve_acting_agent, since it may rewrite ``body.agent_id`` and everything
    downstream (agent resolution, pack narrowing, insert_run_row) must see the
    corrected value.

    ``body.agent_id`` is rewritten in place (RunRequest is a mutable Pydantic model):
    ``bind_agent`` forces it to the principal's own bound agent for a service
    principal that specified none, or refuses ``403 agent_mismatch`` when the body
    named a DIFFERENT agent. A user principal's body passes through unchanged
    (today's A4 behaviour, byte-identical).

    Then ``require_pack_mode`` refuses ``403 prompt_mode_requires_user`` for a service
    principal in prompt mode (no ``pack``/``variant`` set) — a machine caller may only
    drive the narrower, versioned pack graphs, never the open-ended prompt path.

    Raises ``backend.callers.ports.Refusal``; the app-level handler
    (``backend/errors.py:refusal_exception_handler``) converts it to the wire response.
    """
    body.agent_id = bind_agent(principal, body.agent_id)
    require_pack_mode(principal, body.pack is not None)


async def read_scope_for_principal(pool, workspace_id: str, principal: Principal) -> str:
    """The ``read_scope`` governing THIS principal's own reads: its bound agent's
    ``agents.read_scope`` column. ``'own'`` (the fail-closed default) for a user
    principal or an agent-less service principal — ``authorize_run_read`` no-ops for
    ``kind='user'`` regardless of what this returns, so the value only ever matters
    for ``kind='service'``, and only a service principal's own single DB row is ever
    read here (never the run's bound agent, which may differ).
    """
    if principal.kind != "service" or principal.agent_id is None:
        return "own"
    async with workspace_scope(pool, workspace_id) as conn:
        value = await conn.fetchval(
            "SELECT read_scope FROM agents WHERE id = $1::uuid AND workspace_id = $2::uuid",
            principal.agent_id,
            workspace_id,
        )
    return value or "own"


async def authorize_read(
    request,
    principal: Principal,
    run_agent_id: str | None,
    not_found: str = "Run not found",
    *,
    write: bool = False,
) -> None:
    """Scope a get/stream/artifacts/cancel/idempotent-replay route to the calling
    principal, given the run's own ``agent_id`` (already in hand at the call site — no
    DB call at all for ``kind='user'``, so this never changes a user request's DB call
    count). A mismatch is a 404, never 403 — a 403 would tell a caller a run it cannot
    see exists at all.

    The 404 raised is the route's OWN not-found exception (``not_found`` is the exact
    detail that route uses when the row is missing), so the whole body — legacy
    ``detail`` included — is byte-identical to a run that does not exist. It is still a
    ``denials.jsonl`` row naming the principal (PRD §7), written here so no call site
    can forget it.
    """
    if principal.kind == "user":
        return
    workspace_id = principal.workspace_id
    # ``read_scope`` widens reads only. A write (cancel) is always own-agent, or a
    # coordinator key could stop a person's run parked at a gate.
    read_scope = (
        "own"
        if write
        else await read_scope_for_principal(request.app.state.pool, workspace_id, principal)
    )
    try:
        authorize_run_read(principal, run_agent_id, read_scope)
    except Refusal as exc:
        await audit_service_refusal_from(request.app.state.cfg, principal, request.url.path, exc)
        raise HTTPException(status.HTTP_404_NOT_FOUND, not_found) from exc


async def audit_service_refusal_code(cfg, principal: Principal, route: str, code: str) -> None:
    """Fold a refusal's machine ``code`` into the same workspace-scoped
    ``denials.jsonl`` trail ``backend.callers.rest.require_principal`` and
    ``backend.callers.limits.enforce_agent_daily_cap`` already write to.

    Only for ``kind='service'``: a user principal's admission refusals were never
    audited before this task (that behaviour is out of this task's scope — see
    CLAUDE.md "Surgical changes") and stay exactly as they were. Takes the code
    directly rather than an exception object so ONE helper covers both a plain
    ``HTTPException`` (pre-existing admission code — ``resolve_acting_agent`` /
    ``resolve_pack_for_run``) and a ``backend.callers.ports.Refusal`` (``require_human``,
    raised inline in ``decide_gate``).
    """
    if principal.kind != "service":
        return
    scoped_cfg = _workspace_scoped_config(cfg, principal.workspace_id, cfg.repo_root)
    audit.record_refusal(scoped_cfg, "_admission", principal, route, code)


async def audit_service_refusal(cfg, principal: Principal, route: str, exc: HTTPException) -> None:
    """``audit_service_refusal_code`` for a plain ``HTTPException`` site (see there)."""
    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
    code = str(detail.get("code", exc.status_code))
    await audit_service_refusal_code(cfg, principal, route, code)


async def audit_service_refusal_from(cfg, principal: Principal, route: str, exc: Refusal) -> None:
    """``audit_service_refusal_code`` for a ``backend.callers.ports.Refusal`` site."""
    await audit_service_refusal_code(cfg, principal, route, exc.code)
