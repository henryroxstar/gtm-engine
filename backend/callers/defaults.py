"""Default caller verifiers/registries — v1 concrete adapters for PRD §2.1's ports.

JwtUserVerifier replicates backend/deps.py:require_auth's decode + refusal mapping rather
than importing require_auth itself: a later task adapts backend/deps.py to call INTO this
package, and having backend/deps.py import from here while this module imports require_auth
FROM backend/deps.py would cycle. Only the exception type differs from require_auth today
(HTTPException -> Refusal); dependency.refusal_to_http adapts Refusal -> HTTPException at
the route boundary a later task wires up. This module DOES import backend.deps.fetch_entitlement
directly — that function does not import backend.callers, so there is no cycle there.
"""

from __future__ import annotations

import hashlib

import jwt

from gtm_core.capabilities import Entitlement

from .. import auth as _auth
from ..deps import fetch_entitlement
from .ports import Refusal
from .principal import Principal

#: Same ordering as backend/routers/api_keys.py::_ENTITLEMENT_ORDER — kept as a second,
#: independent copy rather than an import because api_keys.py is out of scope for this
#: task (see the file allowlist in the task brief); a later task should consider hoisting
#: this into gtm_core.capabilities as the single source of truth.
_ENTITLEMENT_ORDER: dict[str, int] = {"free": 0, "pro": 1, "pro_plus": 2}


def _cap_entitlement(requested: str, workspace_entitlement: str) -> str:
    """Same rule as backend/routers/api_keys.py::_cap_entitlement — min(requested, live)."""
    if _ENTITLEMENT_ORDER.get(requested, 0) > _ENTITLEMENT_ORDER.get(workspace_entitlement, 0):
        return workspace_entitlement
    return requested


class JwtUserVerifier:
    """kind="user" — a Bearer JWT, mirroring backend/deps.py:require_auth exactly."""

    async def verify(self, raw_token: str, pool) -> Principal:
        try:
            payload = _auth.decode_token(raw_token, expected_type="access")
        except jwt.ExpiredSignatureError as exc:
            raise Refusal(401, "token_expired", "Access token expired") from exc
        except jwt.InvalidTokenError as exc:
            raise Refusal(401, "token_invalid", "Invalid access token") from exc

        workspace_id = payload.get("workspace_id")
        user_id = payload.get("sub")
        if not workspace_id or not user_id:
            raise Refusal(401, "token_invalid", "Invalid access token")

        entitlement = await fetch_entitlement(pool, workspace_id)
        return Principal(
            kind="user",
            subject=user_id,
            workspace_id=workspace_id,
            entitlement=entitlement,
            verifier="jwt",
        )


class ApiKeyServiceVerifier:
    """kind="service" — a raw ``sk-...`` API key, bound to a gtm-engine agent.

    Mirrors mcp_server/auth.py:validate_api_key's lookup, plus the agent binding and
    entitlement capping this port additionally needs. Coded against resolve_api_key()
    returning an ``agent_id`` column — a parallel task (V026) is adding that column to the
    live function; it does not exist on ``dev`` yet, but this verifier never touches a real
    DB (see the test doubles in tests/backend/test_callers_ports.py), so that is fine.
    """

    async def verify(self, raw_token: str, pool) -> Principal:
        if not raw_token or not raw_token.startswith("sk-"):
            raise Refusal(401, "api_key_invalid")
        key_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM resolve_api_key($1)", key_hash)

        if not row:
            # Same code for unknown/revoked/expired — no oracle on WHY a key doesn't work.
            raise Refusal(401, "api_key_invalid")

        workspace_id = str(row["workspace_id"])
        key_id = str(row["key_id"])
        agent_id = row["agent_id"]
        if agent_id is None:
            # The key resolved, so its workspace is known and the refusal belongs on that
            # workspace's ledger. This principal only names who was refused; it is never
            # returned, so it admits nothing.
            raise Refusal(
                403,
                "api_key_unbound",
                principal=Principal(
                    kind="service",
                    subject=key_id,
                    workspace_id=workspace_id,
                    entitlement=Entitlement(str(row["entitlement"])),
                    credential_id=key_id,
                    verifier="api_key",
                    evidence_digest=key_hash[:12],
                ),
            )

        live_entitlement = await fetch_entitlement(pool, workspace_id)
        capped = _cap_entitlement(str(row["entitlement"]), live_entitlement.value)

        return Principal(
            kind="service",
            subject=key_id,
            workspace_id=workspace_id,
            entitlement=Entitlement(capped),
            agent_id=str(agent_id),
            credential_id=key_id,
            verifier="api_key",
            # First 12 hex chars of the SHA-256 hash — enough to correlate ledger rows to a
            # key without ever storing the raw key or the full (still-sensitive) hash.
            evidence_digest=key_hash[:12],
        )


class AgentsTableRegistry:
    """Admits/refuses a ``service`` Principal against its agents row.

    Mirrors backend/services/runs/admission.py:resolve_acting_agent's status codes exactly
    (paused -> 409 agent_paused, anything else non-active -> 409 agent_archived). No row
    -> 404 ``agent_not_found`` with that function's message, "Unknown agent" — the same
    code the error envelope derives from that message on the A4 path.
    """

    async def admit(self, principal: Principal, conn) -> None:
        if principal.kind != "service" or principal.agent_id is None:
            return
        row = await conn.fetchrow(
            "SELECT status, packs, monthly_budget_usd, read_scope, daily_dispatch_cap "
            "FROM agents WHERE id = $1::uuid AND workspace_id = $2::uuid",
            principal.agent_id,
            principal.workspace_id,
        )
        if row is None:
            raise Refusal(404, "agent_not_found", "Unknown agent", principal=principal)
        if row["status"] == "paused":
            raise Refusal(409, "agent_paused", principal=principal)
        if row["status"] != "active":
            raise Refusal(409, "agent_archived", principal=principal)
