"""Service-to-service wallet top-up (the billing service -> this service) — MM7.

This service holds no price. When the billing service has taken a payment and decided what it is worth,
it POSTs the result here and this route credits the workspace's wallet. It is the payment channel
INSTEAD of the RevenueCat webhook, never alongside it: ``backend/payment_channel.py`` picks exactly one,
and this route answers 409 while the webhook is the live one.

Auth is ``PUT /v1/entitlement``'s, deliberately the same: the shared service secret
(``BILLING_SYNC_SECRET``), NEVER a user token — this route MINTS spendable credit, so a user JWT
reaching it would be free money. A missing secret refuses every call.

The bound on one top-up (``0 < amount_usd <= 500``) is checked HERE rather than as a pydantic
constraint: a schema constraint 422s before any handler code runs, and a refusal that never reaches
the handler cannot be audited. Every refusal, duplicate and unknown workspace leaves a structured log
line naming the workspace and the order, and — where the workspace has a tree on disk — a
``denials.jsonl`` row in it.
"""

from __future__ import annotations

import logging
import math
import re
from types import SimpleNamespace
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..deps import require_service_auth
from ..payment_channel import billing_service_active
from ..schemas import ERROR_RESPONSES, WalletTopUpRequest, WalletTopUpResponse
from . import webhooks

log = logging.getLogger(__name__)

router = APIRouter(prefix="/wallet", tags=["wallet"], responses=ERROR_RESPONSES)

#: The most one top-up may add. A bound on the blast radius of a bad caller, not a price.
MAX_TOP_UP_USD = 500.0
#: Credits per dollar is a METERING unit (it is how a balance is counted), not a price.
PROVIDER = "billing_service"

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _audit(workspace_id: str, order_id: str, outcome: str, detail: str) -> None:
    """A structured line, and a ``denials.jsonl`` row when the workspace is known on this host."""
    log.warning(
        "wallet top-up %s: workspace=%s order_id=%s %s", outcome, workspace_id, order_id, detail
    )
    try:
        from gtm_core.ledgers import Ledgers
        from gtm_core.paths import workspace_content_root, workspace_tree

        if not workspace_tree(workspace_id).is_dir():
            return
        Ledgers(
            SimpleNamespace(content_root=workspace_content_root(workspace_id)), "_billing"
        ).append_denial(
            {
                "tool": "wallet_top_up",
                "decision": "deny",
                "outcome": "denied",
                "detail": f"{outcome}: order_id={order_id} {detail}"[:300],
                "source": "billing-service",
            }
        )
    except Exception:  # noqa: BLE001 — observability must never change the answer to a payment
        log.debug("could not write the top-up audit row for %s", workspace_id, exc_info=True)


@router.post("/{workspace_id}/top-ups", status_code=status.HTTP_200_OK)
async def top_up(
    workspace_id: str,
    body: WalletTopUpRequest,
    request: Request,
    _: Annotated[None, Depends(require_service_auth)],
) -> WalletTopUpResponse:
    """Credit a workspace's wallet with a payment the billing service has priced. Idempotent per ``order_id``."""
    if not _UUID_RE.match(workspace_id):
        # No workspace to name a tree after, so a log line only — but a payment that names nobody is exactly
        # the one somebody will ask about.
        log.warning(
            "wallet top-up refused: workspace_id is not a UUID: %r order_id=%s",
            workspace_id[:80],
            body.order_id,
        )
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "workspace_id must be a UUID")
    if not billing_service_active():
        # A payment the billing service has already taken, refused. Silent, it reads as "the integration
        # stopped working" — and a non-exact setting value (``enabled``) is enough to cause it.
        _audit(
            workspace_id,
            body.order_id,
            "payment_channel_inactive",
            f"amount_usd={body.amount_usd!r}",
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "payment_channel_inactive",
                "message": "The billing-service top-up channel is not the active payment channel.",
            },
        )
    amount = body.amount_usd
    if not (math.isfinite(amount) and 0 < amount <= MAX_TOP_UP_USD):
        _audit(workspace_id, body.order_id, "amount_out_of_bounds", f"amount_usd={amount!r}")
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {
                "code": "amount_out_of_bounds",
                "message": f"amount_usd must be greater than 0 and at most {MAX_TOP_UP_USD:g}",
            },
        )

    pool: Any = request.app.state.pool
    outcome = await webhooks.apply_wallet_top_up(
        pool=pool,
        workspace_id=workspace_id,
        amount_usd=float(amount),
        provider=PROVIDER,
        external_transaction_id=body.order_id,
    )
    if outcome != "new":
        _audit(workspace_id, body.order_id, outcome, f"amount_usd={amount!r}")
    return WalletTopUpResponse(applied=(outcome == "new"), outcome=outcome)
