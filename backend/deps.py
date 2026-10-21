"""FastAPI dependency injection — auth, workspace scoping, tier gating.

Every protected route declares `ws: WorkspaceCtx = Depends(require_auth)`.
This:
  1. Extracts and validates the Bearer JWT.
  2. Returns a WorkspaceCtx with the workspace ID + entitlement.

Routes that need a DB connection use `Depends(get_db_for_workspace)` which
opens a workspace_scope() connection (RLS active for the request lifetime).

Tier gating:
  @router.post("/runs")
  async def post_run(..., _: None = Depends(require_tier(Tier.PIPELINE))):
      ...
"""

from __future__ import annotations

import hmac
import logging
import os
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from gtm_core.capabilities import Entitlement
from gtm_core.tiers import Tier

from . import auth as _auth
from .database import workspace_scope

log = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

# nosec B105 — an env-var *name*, not a credential (the credential is os.getenv's result,
# never a literal here). Same false-positive shape as backend/push.py's _TOKEN_SCOPE.
_SERVICE_SECRET_ENV = "BILLING_SYNC_SECRET"  # nosec B105

# RFC 6750 §3: no error param when no credentials were sent; ``invalid_token`` otherwise.
_CHALLENGE_MISSING = {"WWW-Authenticate": "Bearer"}
_CHALLENGE_INVALID = {"WWW-Authenticate": 'Bearer error="invalid_token"'}
_CHALLENGE_EXPIRED = {
    "WWW-Authenticate": 'Bearer error="invalid_token", error_description="token expired"'
}


# ── workspace context ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WorkspaceCtx:
    user_id: str
    workspace_id: str
    entitlement: Entitlement


async def fetch_entitlement(pool, workspace_id: str) -> Entitlement:
    """Live DB lookup — a workspace with no ``subscriptions`` row (never synced by the
    billing service) is FREE, never an open-ended default.

    Shared by ``require_auth`` (per-request) and ``runs.reconcile_gates`` (startup
    resume of a stranded gated run, outside a request context — no ``WorkspaceCtx``
    exists yet there, only the row's ``workspace_id``).
    """
    async with workspace_scope(pool, workspace_id) as conn:
        row = await conn.fetchrow(
            "SELECT entitlement FROM subscriptions WHERE workspace_id = $1",
            workspace_id,
        )
    return Entitlement(row["entitlement"]) if row else Entitlement.FREE


async def require_auth(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> WorkspaceCtx:
    """Validate Bearer JWT and return workspace context. 401 on failure.

    Only an authentic token is ever reported as ``token_expired``: PyJWT verifies the
    signature before the claims, so a forged expired token is ``token_invalid``. The
    library's own message is never forwarded.
    """
    if creds is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            {"code": "token_missing", "message": "Missing bearer token"},
            headers=_CHALLENGE_MISSING,
        )
    try:
        payload = _auth.decode_token(creds.credentials, expected_type="access")
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            {"code": "token_expired", "message": "Access token expired"},
            headers=_CHALLENGE_EXPIRED,
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            {"code": "token_invalid", "message": "Invalid access token"},
            headers=_CHALLENGE_INVALID,
        ) from exc

    workspace_id = payload.get("workspace_id")
    user_id = payload.get("sub")
    if not workspace_id or not user_id:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            {"code": "token_invalid", "message": "Invalid access token"},
            headers=_CHALLENGE_INVALID,
        )

    # Fetch live from DB so the billing service's entitlement sync (PUT
    # /v1/entitlement/{workspace_id}) takes effect immediately, no re-login needed.
    entitlement = await fetch_entitlement(request.app.state.pool, workspace_id)

    return WorkspaceCtx(
        user_id=user_id,
        workspace_id=workspace_id,
        entitlement=entitlement,
    )


# ── service-to-service auth (the billing service → entitlement sync) ──────────────


def require_service_auth(request: Request) -> None:
    """Authenticate a service call from the billing service. 401 on failure.

    NOT user auth. The entitlement-sync route this guards GRANTS a workspace's
    entitlement (a free self-upgrade to pro_plus if abused), so it must never accept
    a user JWT — it compares the ``Authorization`` header constant-time against the
    shared ``BILLING_SYNC_SECRET`` and nothing else. A valid user token won't equal the
    secret, so it is rejected here.

    A missing secret refuses ALL calls (500) rather than failing open — the same posture
    the former RevenueCat webhook used for its secret.
    """
    secret = os.getenv(_SERVICE_SECRET_ENV)
    if not secret:
        log.error("%s not set — refusing all entitlement-sync calls", _SERVICE_SECRET_ENV)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Service auth not configured",
        )
    header = request.headers.get("Authorization")
    if not header or not hmac.compare_digest(header, secret):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            {"code": "service_auth_invalid", "message": "Invalid service authorization"},
        )


# ── DB connection scoped to the authenticated workspace ───────────────────────


class _WorkspaceScopedConn:
    """Async context manager returned by get_db_for_workspace."""

    def __init__(self, pool, workspace_id: str):
        self._pool = pool
        self._workspace_id = workspace_id
        self._ctx = None

    async def __aenter__(self):
        self._ctx = workspace_scope(self._pool, self._workspace_id)
        return await self._ctx.__aenter__()

    async def __aexit__(self, *args):
        return await self._ctx.__aexit__(*args)


def get_db(ws: Annotated[WorkspaceCtx, Depends(require_auth)]):
    """Dependency: yields a workspace-scoped DB connection (RLS active)."""

    def _inner(request: Request):
        return _WorkspaceScopedConn(request.app.state.pool, ws.workspace_id)

    return _inner


# ── tier gate ─────────────────────────────────────────────────────────────────


def require_tier(tier: Tier):
    """Dependency factory: 403 if the workspace's entitlement can't reach `tier`.

    Usage: `_: None = Depends(require_tier(Tier.PIPELINE))`

    Gates an ENDPOINT on a capability class, so it resolves the tier's own
    ``[defaults]`` floor from ``gtm_core/gating.toml`` rather than going through
    ``resolve_effective``. The previous form built a synthetic ``_gate_probe``
    skill to interrogate the resolver; once the resolver started reading a per-SKILL
    floor (2026-08-25) that probe resolved as an unregistered name and fail-closed to
    ``pro_plus``, which would have 403'd PRO workspaces here. A tier gate has no skill
    to name, so it asks the tier question directly.
    """
    from gtm_core.capabilities import entitlement_meets
    from gtm_core.gating import tier_floor

    def _check(ws: Annotated[WorkspaceCtx, Depends(require_auth)]) -> None:
        if not entitlement_meets(ws.entitlement, tier_floor(tier)):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Your plan does not include {tier.value!r} features.",
            )

    return _check
