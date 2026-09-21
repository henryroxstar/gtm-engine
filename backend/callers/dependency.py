"""Caller admission — the pieces a FastAPI route boundary composes (a later task wires
these into backend/deps.py; nothing here is called from a route yet).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from fastapi import HTTPException

from .ports import AgentRegistry, CallerVerifier, Refusal
from .principal import Principal, is_admitted_kind

logger = logging.getLogger(__name__)

#: PRD §2.1 rule 4: an allowlist, not a denylist — a kind no rule has ever seen (a future
#: D1 delegated_agent, or anything else) is refused by default, never admitted by default.
GATE_ADMITS: frozenset[str] = frozenset({"user"})


async def verify_chain(
    raw_token: str, pool, verifiers: Sequence[CallerVerifier], *, timeout_s: float = 5.0
) -> Principal:
    """Establish a Principal from a raw credential by a fixed 2-way dispatch on token
    prefix — never an iterate-until-one-works loop. PRD §2.1 rule 1: identity comes from
    ONE verifier; probing several until one happens to accept a token would let a
    malformed/foreign credential be matched to the wrong verifier by trial rather than by
    its own shape.

    ``verifiers`` is exactly two entries, in a fixed order: ``(user_verifier,
    service_verifier)``. A raw token starting with ``sk-`` is routed to the SERVICE
    verifier only; every other token goes to the USER verifier only.

    PRD §2.1 rule 4 is enforced HERE, structurally, on every Principal any verifier
    returns — not merely absent from today's verifiers by coincidence. A kind outside
    ``V1_ADMITTED_KINDS`` (in v1, only the D1-reserved ``delegated_agent``) or a non-null
    ``on_behalf_of`` (also D1-reserved) is refused before the Principal ever reaches a
    caller, so a future verifier that starts emitting one does not silently widen v1.

    PRD §2.1 rule 3 (fail closed) is enforced on the verifier call itself, mirroring
    ``admit_all``'s treatment of a registry: a verifier that raises or times out is a
    refusal, never a bare exception left to propagate into a generic, unaudited 500.
    """
    if len(verifiers) != 2:
        raise Refusal(500, "verify_chain_misconfigured")
    user_verifier, service_verifier = verifiers
    verifier = service_verifier if raw_token and raw_token.startswith("sk-") else user_verifier
    try:
        principal = await asyncio.wait_for(verifier.verify(raw_token, pool), timeout=timeout_s)
    except Refusal:
        raise
    except TimeoutError as exc:
        raise Refusal(503, "verifier_timeout") from exc
    except Exception as exc:  # noqa: BLE001 — must never surface as a bare, unrelated 500
        logger.warning("verifier %r raised during admission", verifier, exc_info=True)
        raise Refusal(500, "verifier_error") from exc
    if not is_admitted_kind(principal.kind) or principal.on_behalf_of is not None:
        raise Refusal(403, "delegation_not_supported", principal=principal)
    return principal


async def admit_all(
    principal: Principal,
    registries: Sequence[AgentRegistry],
    conn,
    *,
    timeout_s: float = 5.0,
) -> None:
    """PRD §2.1 rule 2: external and local verdicts combine by AND. Rule 3: everything
    fails closed — a registry that errors, times out, or answers "unknown" is a refusal.

    Registries are polled in order and this function returns on the FIRST refusal
    (short-circuit), rather than always polling every registry and ANDing the results.
    Both are fail-closed-correct (either way the caller only ever sees "allow" when
    every registry allowed), but short-circuiting additionally: (a) never spends a call
    on an optional/external registry once a local one has already refused — the PRD's
    "an optional adapter's allow cannot override a local deny" case holds by construction,
    not by discarding a value; and (b) never lets a later registry's own side effects
    (e.g. its own audit write) run after the decision is already "refuse".
    """
    for registry in registries:
        try:
            await asyncio.wait_for(registry.admit(principal, conn), timeout=timeout_s)
        except Refusal:
            raise
        except TimeoutError as exc:
            # 503, not 403/409: a timeout is an infrastructure failure of the admission
            # check itself, not a policy verdict about the caller. Still fail-closed —
            # this still refuses unconditionally, exactly like any other Refusal.
            raise Refusal(503, "registry_timeout", principal=principal) from exc
        except Exception as exc:  # noqa: BLE001 — must never surface as a bare, unrelated 500
            logger.warning("registry %r raised during admission", registry, exc_info=True)
            raise Refusal(500, "registry_error", principal=principal) from exc


def require_human(principal: Principal) -> None:
    """PRD §2.1 rule 4: allowlist, not denylist. Only ``kind="user"`` clears a human gate —
    everything else, including a kind no rule has ever named, is refused."""
    if principal.kind not in GATE_ADMITS:
        raise Refusal(403, "human_approval_required", principal=principal)


def require_pack_mode(principal: Principal, is_pack_mode: bool) -> None:
    """A machine caller may only drive the narrower, versioned pack graphs — never the
    open-ended prompt path (PRD §2.1 rule 5: machines narrow, they never widen)."""
    if principal.kind == "service" and not is_pack_mode:
        raise Refusal(403, "prompt_mode_requires_user", principal=principal)


def bind_agent(principal: Principal, requested_agent_id: str | None) -> str | None:
    """Resolve the agent id a run binds to.

    ``kind="user"``: today's A4 behavior, untouched — whatever the caller requested (or
    None) passes straight through.

    Any other kind (in v1, only ``kind="service"`` ever reaches here — ``delegated_agent``
    is refused by every verifier before a Principal exists): the caller IS a gtm-engine
    agent, so it can never request a DIFFERENT one (PRD §2.1 rule 5, narrow-never-widen) —
    a mismatched request is refused rather than silently overridden, and an absent request
    is forced to the caller's own bound agent rather than left unbound.
    """
    if principal.kind == "user":
        return requested_agent_id
    if requested_agent_id is not None and requested_agent_id != principal.agent_id:
        raise Refusal(403, "agent_mismatch", principal=principal)
    return principal.agent_id


def authorize_run_read(
    principal: Principal, run_agent_id: str | None, read_scope: str = "own"
) -> None:
    """PRD §2.1 G0 "Read and stream its own runs": scope a get/list/stream/artifacts/
    cancel/replay route to the calling principal. Takes the run's OWN agent_id — the
    caller already has the run row in hand — and does no DB I/O itself.

    ``read_scope`` is a plain parameter, not a ``Principal`` field: it reaches this
    function because the REST route looks up the bound agent's own ``agents.read_scope``
    column (default ``"own"``, widened to ``"workspace"`` for a coordinator agent — see
    the PRD) and passes the value in. Keeping it a parameter rather than a Principal
    field keeps this function DB-free and trivial to unit test.

    ``kind="user"``: today's behavior, untouched — always allowed, no extra work, for
    any ``run_agent_id`` including ``None``.

    ``kind="service"``: allowed unconditionally when ``read_scope == "workspace"`` (the
    bound agent opted into seeing every run in its workspace); otherwise (the ``"own"``
    default) allowed only when ``run_agent_id`` matches the principal's own ``agent_id``.

    A mismatch refuses with a **404**, never 403 (PRD §2.1 rule 3: "a 404 and an explicit
    'not authorized' are the same answer") — a 403 would tell a caller a run it cannot
    see exists at all.
    """
    if principal.kind == "user":
        return
    if read_scope == "workspace":
        return
    if run_agent_id != principal.agent_id:
        raise Refusal(404, "run_not_found", principal=principal)


def refusal_to_http(exc: Refusal) -> HTTPException:
    """Adapt a Refusal to the detail-dict shape backend/deps.py and
    backend/services/runs/admission.py already use at their route boundaries: always a
    ``code``, plus ``message`` only when the Refusal carried one.

    A 401 carries the same RFC 6750 challenge ``require_auth`` sends, so a person's bearer
    failure reads identically on a route that moved to ``require_principal``."""
    from backend.deps import _CHALLENGE_EXPIRED, _CHALLENGE_INVALID

    detail = {"code": exc.code, "message": exc.message} if exc.message else {"code": exc.code}
    headers = None
    if exc.status_code == 401:
        headers = _CHALLENGE_EXPIRED if exc.code == "token_expired" else _CHALLENGE_INVALID
    return HTTPException(exc.status_code, detail, headers=headers)
