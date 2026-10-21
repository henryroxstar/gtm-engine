"""Cost metering and budget guard for MCP tool calls.

Thin adapters over the shared metering contract (:mod:`gtm_core.metering`) — the
SQL and the cost-record schema now live in one place. The two public functions
keep their original signatures so ``mcp_server/server.py`` call sites are unchanged:

  1. check_budget(workspace_id, pool)  — pre-call guard (§R2).
  2. meter_call(...)                    — post-call record.

Both run RLS-subject: the workspace is known post-auth, so each acquires a
``workspace_scope`` connection (SET LOCAL app.current_workspace_id) and passes it
to the shared contract via ``conn=``. The ``mcp_calls`` INSERT then satisfies its
``WITH CHECK (workspace_id = current_workspace_id())`` under FORCE RLS as gtm_api.

A failed DEBIT is not best-effort (PRD 2026-09-22 §4.B, resolved 2026-10-14). The call has
already run and the money is already spent, so swallowing the failure would turn a recorded
charge into free usage. The rule:

  - debt is recorded, never erased: the debit is unclamped, so an overrun leaves a negative
    balance (which :func:`check_budget` already refuses);
  - a debit that fails SURFACES as :class:`DebitFailedError` and is marked durably on the wallet
    (``workspace_wallets.debit_failed_at``), in a transaction of its own;
  - the NEXT call is refused with :class:`BillingUnavailableError` — by every gate that decides
    whether a workspace may spend — until an operator reconciles and clears the marker;
  - the CURRENT call is never failed for it: a tool that already produced its result still
    returns it (see ``draft_post`` / ``draft_outreach``).

Only an unreadable budget *read* is a judgement call about availability; a known failed debit is
not.
"""

from __future__ import annotations

import logging

from gtm_core import payment_required
from gtm_core.db import workspace_scope
from gtm_core.metering import (
    BillingUnavailableError,
    DebitFailedError,
    InsufficientCreditsError,
    acheck_budget,
    mark_debit_failed,
    reserve_credits,
    settle_credits,
    wallet_has_credits,
)

_log = logging.getLogger(__name__)

__all__ = [
    "BillingUnavailableError",
    "DebitFailedError",
    "InsufficientCreditsError",
    "check_budget",
    "debit_or_mark",
    "debit_or_mark_quietly",
    "meter_call",
    "reserve_credits",
    "settle_credits",
]


async def check_budget(workspace_id: str, pool) -> bool:
    """Return False if the workspace has exceeded its monthly cost cap.

    Raises BillingUnavailableError if an earlier debit for the workspace failed and has not been
    reconciled — whatever its balance (that is not a payment problem, so not the 402 below).
    Raises InsufficientCreditsError if the workspace wallet balance <= 0.00 and no active
    subscription is under cap.
    Fail-open on DB connection error (no hard ceiling) — MCP is metered per-call and reconcilable.
    Runs inside workspace_scope so the budget read is RLS-subject (gtm_api).
    """
    async with workspace_scope(pool, workspace_id) as conn:
        wallet_row = await conn.fetchrow(
            "SELECT balance_credits, balance_usd, debit_failed_at FROM workspace_wallets "
            "WHERE workspace_id = $1::uuid",
            workspace_id,
        )
        if wallet_row is not None and wallet_row.get("debit_failed_at") is not None:
            raise BillingUnavailableError()

        # 1. Positive prepaid wallet balance allows execution
        if wallet_row is not None and wallet_has_credits(
            wallet_row.get("balance_credits"), wallet_row.get("balance_usd")
        ):
            return True

        # 2. Check if an active subscription with positive cap covers the call
        sub_row = await conn.fetchrow(
            "SELECT monthly_cost_cap_usd, status FROM subscriptions WHERE workspace_id = $1::uuid",
            workspace_id,
        )
        has_active_sub = (
            sub_row is not None
            and sub_row["status"] in ("active", "trialing")
            and float(sub_row["monthly_cost_cap_usd"] or 0) > 0
        )
        if has_active_sub:
            sub_allowed = await acheck_budget(
                pool,
                workspace_id,
                table="unified_metering_log",
                hard_ceiling_multiplier=None,
                conn=conn,
            )
            if sub_allowed:
                return True

        raise InsufficientCreditsError(payment_required.text("credits"))


async def debit_or_mark(
    reservation_id: str,
    actual_credits: float,
    *,
    pool,
    workspace_id: str,
    **audit,
) -> None:
    """Debit a finished call; if the debit fails, mark the wallet and raise ``DebitFailedError``.

    ``settle_credits(raise_on_error=True)`` writes the wallet debit and the audit row in ONE
    transaction, so a failure leaves neither half behind. That transaction has rolled back by the
    time this marks the wallet — in a transaction of its own (``mark_debit_failed``) — so the
    NEXT call is refused. Callers that already hold a result decide what to do with the error;
    this logs it either way, because a failed debit is money not collected.
    """
    try:
        await settle_credits(
            reservation_id,
            actual_credits,
            pool=pool,
            workspace_id=workspace_id,
            raise_on_error=True,
            **audit,
        )
    except DebitFailedError:
        marked = await mark_debit_failed(pool, workspace_id)
        _log.error(
            "debit failed for workspace %s (%s, %.4f credits uncollected); wallet %s",
            workspace_id,
            audit.get("tool_name", "?"),
            actual_credits,
            "marked, next call will be refused" if marked else "NOT marked — reconcile by hand",
            exc_info=True,
        )
        raise


async def debit_or_mark_quietly(
    reservation_id: str,
    actual_credits: float,
    *,
    pool,
    workspace_id: str,
    **audit,
) -> bool:
    """:func:`debit_or_mark` for a caller that already holds its result: never raises.

    A tool that has produced (and paid for) its output must still return it. Raising out of a
    ``finally`` would mask the call's own error and drop that result, so a failed debit is
    marked and logged by ``debit_or_mark`` — the NEXT call is what gets refused — and this
    returns whether the call was debited.
    """
    try:
        await debit_or_mark(
            reservation_id, actual_credits, pool=pool, workspace_id=workspace_id, **audit
        )
    except DebitFailedError:
        return False
    return True


async def meter_call(
    *,
    workspace_id: str,
    api_key_id: str,
    tool_name: str,
    profile_name: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
    cost_credits: float | None = None,
    pool,
) -> None:
    """Deduct a finished call from the workspace wallet and append its ``unified_metering_log`` row.

    A thin adapter on :func:`debit_or_mark` (-> ``settle_credits``): the debit is unclamped, so an
    overrun is recorded as debt, and the wallet UPDATE and the audit INSERT are one transaction.
    Raises :class:`DebitFailedError` if it cannot be written — see the module docstring.
    """
    credits = cost_credits if cost_credits is not None else round(cost_usd * 1000.0, 4)
    await debit_or_mark(
        "",
        credits,
        pool=pool,
        workspace_id=workspace_id,
        runtime="mcp",
        tool_name=tool_name,
        profile_name=profile_name,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
        api_key_id=api_key_id,
    )
