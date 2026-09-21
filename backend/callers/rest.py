"""FastAPI-facing caller admission (PRD §2.1 G0) — the Depends() a route uses in place
of (or alongside) backend/deps.py:require_auth once it must also admit a machine
(``service``) caller. Nothing here is wired into a route yet; a not-yet-dispatched REST
task does that.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.database import workspace_scope
from backend.deps import _CHALLENGE_MISSING
from backend.session import _workspace_scoped_config

from . import audit
from .defaults import AgentsTableRegistry, ApiKeyServiceVerifier, JwtUserVerifier
from .dependency import admit_all, refusal_to_http, verify_chain
from .ports import Refusal
from .principal import Principal

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


async def require_principal(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    """Establish and admit the calling Principal for a route that accepts both a user
    JWT and a service API key. Mirrors backend/deps.py:require_auth's credential
    extraction (the same ``HTTPBearer(auto_error=False)`` dependency) and its
    ``request.app.state.pool`` access pattern, so it drops into a route the same way.

    Order: verify the credential (``verify_chain``, which also enforces the §2.1 rule 4
    kind/``on_behalf_of`` allowlist structurally) -> if the resolved principal is bound
    to an agent, admit it against that agent's row (``admit_all`` + the default
    ``AgentsTableRegistry``). A principal with no ``agent_id`` (a plain user run) has
    nothing to admit against, so registry admission is skipped for it — the same
    treatment ``backend/services/runs/admission.py:resolve_acting_agent`` already gives
    ``agent_id is None``.

    Any ``Refusal`` from either step is audited best-effort
    (``audit.record_refusal``, which never raises) and then re-raised as the matching
    ``HTTPException`` via ``refusal_to_http``. On success ``request.state.principal`` is
    set (so downstream code — logging, attribution — can read it without threading it
    through every call) and the ``Principal`` is returned, mirroring
    ``require_auth``'s ``WorkspaceCtx`` return.
    """
    if creds is None:
        # No credential at all: require_auth's own answer, byte for byte.
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            {"code": "token_missing", "message": "Missing bearer token"},
            headers=_CHALLENGE_MISSING,
        )
    pool = request.app.state.pool
    raw_token = creds.credentials
    try:
        principal = await verify_chain(
            raw_token, pool, [JwtUserVerifier(), ApiKeyServiceVerifier()]
        )
        if principal.agent_id is not None:
            # agents is FORCE RLS: outside workspace_scope the runtime role sees zero rows,
            # which refused every bound key as "unknown agent".
            async with workspace_scope(pool, principal.workspace_id) as conn:
                await admit_all(principal, [AgentsTableRegistry()], conn)
    except Refusal as exc:
        if exc.principal is not None:
            # A workspace IS known: `request.app.state.cfg` is the GLOBAL, single-tenant
            # config — writing to its content_root would land this refusal in the wrong
            # tenant's tree. Scope it exactly the way BackendSessionStore.connect() does
            # before any run touches disk. Read lazily (REST wiring, Fleet Phase A, Task
            # 3): a route on the SUCCESS path never needs `cfg` at all, and several
            # backend test fixtures set `app.state.pool` but not `app.state.cfg`.
            cfg = request.app.state.cfg
            scoped_cfg = _workspace_scoped_config(cfg, exc.principal.workspace_id, cfg.repo_root)
            # No run/pack/profile has been resolved yet at this pre-run admission layer, so
            # there's no real content profile to attribute this to. "_admission" is a fixed,
            # workspace-scoped bucket for pre-run admission refusals only — not a real content
            # profile. TODO(REST wiring): once this call site can see the resolved agent, prefer
            # its actual profile instead of this sentinel.
            audit.record_refusal(
                scoped_cfg, "_admission", exc.principal, request.url.path, exc.code
            )
        else:
            # No workspace was ever resolved (unknown/revoked/expired key) — genuinely
            # unattributable, so there is no per-workspace denials.jsonl to write it into.
            # Log it structurally instead; never the raw credential (not in scope here anyway).
            logger.warning(
                "caller admission refused for a credential that resolved no workspace "
                "(route=%s, code=%s)",
                request.url.path,
                exc.code,
            )
        raise refusal_to_http(exc) from exc

    request.state.principal = principal
    return principal
