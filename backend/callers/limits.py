"""Per-agent daily dispatch ceilings and per-principal rate limiting (Fleet Phase A, G7).

Two independent admission checks, each following the "raise ``Refusal``, never build
an ``HTTPException`` here" convention the rest of this package uses
(``backend/callers/dependency.py:refusal_to_http`` is still the one adapter). Both are
wired into the run and pack routes in ``backend/routers/``.

``enforce_agent_daily_cap`` narrows a run's agent against its own
``agents.daily_dispatch_cap`` column, for every caller kind — narrowing-only, like
``backend/agents.py:acheck_agent_budget``. It differs on a read error: a count that
cannot be taken is ``503 daily_cap_unavailable``, never reported as the cap being
reached.

``enforce_principal_rate`` is an ADDITIONAL, principal-keyed limit stacked on top of
``backend/ratelimit.py``'s existing per-IP limiter — it never replaces it, and a
``kind="user"`` principal is completely untouched (today's user behavior stays
byte-identical). It reuses the SAME ``limits`` storage backend (``memory://`` or the
configured Redis) as ``backend.ratelimit.limiter``, so a multi-worker deployment shares
one counter, and it raises ``Refusal`` directly rather than going through slowapi's own
``RateLimitExceeded`` → the existing generic ``rate_limit_exceeded`` handler in
``backend/errors.py``. That is the smallest change that gets a DISTINCT code
(``principal_rate_limited``) to the client for this limit versus the existing per-IP
``rate_limit_exceeded`` one: no change to ``backend/errors.py`` or ``backend/main.py``
is needed, because this path never raises slowapi's ``RateLimitExceeded`` in the first
place — it calls the same underlying ``limits`` strategy object directly.

Rate choice: 60/minute per service principal (``api_keys.id``, i.e. ``Principal.subject``
for ``kind="service"``). ``create_run``'s existing per-IP limit is 30/minute
(``backend/routers/runs.py``); this is deliberately a bit looser per-credential than
that per-IP ceiling, because it is a SECOND, additive ceiling stacked on top of it (one
IP can host several distinct service keys, and each keeps its own bucket here), not a
replacement — the per-IP limit remains the tighter shared backstop.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import Depends
from limits import parse as _parse_rate_limit

from backend.agents import acheck_agent_daily_cap
from backend.database import workspace_scope
from backend.ratelimit import limiter
from backend.session import _workspace_scoped_config

from . import audit
from .ports import Refusal
from .principal import Principal
from .rest import require_principal

logger = logging.getLogger(__name__)

_PRINCIPAL_RATE_LIMIT = "60/minute"
_principal_rate_item = _parse_rate_limit(_PRINCIPAL_RATE_LIMIT)


async def enforce_agent_daily_cap(
    pool: Any, cfg: Any, principal: Principal, agent_row: dict | None, route: str
) -> None:
    """Refuse a dispatch once the run's agent (``agent_row``) has hit its own
    ``daily_dispatch_cap`` for the current UTC day — whoever the caller is.

    No-op when the run has no agent, or the agent carries no cap (the workspace/
    entitlement caps still apply elsewhere) — mirrors ``acheck_agent_budget``'s
    "no agent / no cap -> allow" shape, and does zero DB work in either case.

    On refusal, audits FIRST — workspace-scoped, exactly as
    ``backend.callers.rest.require_principal`` does for its own admission refusals
    (this refusal always has a known workspace, the principal's own, so it never takes
    the "unattributable" log-only path) — and THEN raises.
    """
    if agent_row is None or agent_row.get("daily_dispatch_cap") is None:
        return
    try:
        async with workspace_scope(pool, principal.workspace_id) as conn:
            allowed = await acheck_agent_daily_cap(
                conn,
                principal.workspace_id,
                agent_row["agent_id"],
                agent_row["daily_dispatch_cap"],
            )
    except Exception as exc:  # noqa: BLE001 — cannot count: refuse, but not as "cap reached"
        logger.warning("daily dispatch cap check failed (route=%s)", route, exc_info=True)
        _audit(cfg, principal, route, "daily_cap_unavailable")
        raise Refusal(503, "daily_cap_unavailable", principal=principal) from exc
    if allowed:
        return
    _audit(cfg, principal, route, "agent_daily_cap_reached")
    raise Refusal(429, "agent_daily_cap_reached", principal=principal)


def _audit(cfg: Any, principal: Principal, route: str, code: str) -> None:
    if cfg is None:
        from agent.config import Config

        cfg = Config.from_env()
    scoped_cfg = _workspace_scoped_config(cfg, principal.workspace_id, cfg.repo_root)
    audit.record_refusal(scoped_cfg, "_admission", principal, route, code)


def enforce_principal_rate(
    principal: Annotated[Principal, Depends(require_principal)],
) -> None:
    """An ADDITIONAL rate limit for ``kind="service"`` callers only.

    A plain FastAPI dependency (not a ``@limiter.limit(...)`` decorator) because it
    keys off the caller's credential rather than the request's IP. It takes the
    principal from ``require_principal`` itself — FastAPI resolves that once per request
    and shares it — so it cannot run ahead of admission and silently limit nothing. A
    ``kind="user"`` principal never touches the limiter storage, so a person's quota is
    unchanged.
    """
    if principal.kind != "service":
        return
    if not limiter.enabled:
        return
    key = f"principal:service:{principal.subject}"
    if not limiter.limiter.hit(_principal_rate_item, key):
        raise Refusal(429, "principal_rate_limited", principal=principal)
