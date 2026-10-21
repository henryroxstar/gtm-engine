"""The checks and helpers that come before any spend, and the readiness blockers (W1; see ``provision.py``)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException, status
from pydantic import SecretStr

from gtm_core.capabilities import Entitlement
from gtm_core.metering import wallet_has_credits
from gtm_core.paths import workspace_profiles_root

from ..database import workspace_scope
from ..deps import WorkspaceCtx
from .integrations import get_workspace_configured_providers, upsert_credential

log = logging.getLogger(__name__)


def _how_to_fix(kind: str, name: str) -> str:
    if kind == "integration":
        return f"Add your {name} API key (PUT /v1/integrations/{name}), then repeat this request."
    if kind == "knowledge":
        return (
            f"Add or refresh the '{name}' knowledge topic in the profile, then repeat this request."
        )
    if kind == "context":
        return (
            f"Provide the required input '{name}' when you start a run, then repeat this request."
        )
    if kind == "setting":
        return f"Add '{name}' to the profile's PROFILE.md, then repeat this request."
    return "Fix this item (see what_is_missing), then repeat this request."


async def precheck(pool: Any, ws: WorkspaceCtx) -> None:
    """Live entitlement, a wallet with credit, and the §R2 cap. Repeated on EVERY call, creates nothing."""
    from .runs.budget import admits

    if ws.entitlement == Entitlement.FREE:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            {
                "code": "entitlement_required",
                "message": "Setting up an agent needs a paid plan.",
                "next_step": "Upgrade your plan, then repeat this request.",
            },
        )
    async with workspace_scope(pool, ws.workspace_id) as conn:
        wallet = await conn.fetchrow(
            "SELECT balance_usd, balance_credits FROM workspace_wallets WHERE workspace_id = $1::uuid",
            ws.workspace_id,
        )
    if wallet is None or not wallet_has_credits(wallet["balance_credits"], wallet["balance_usd"]):
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            {
                "code": "insufficient_credit",
                "message": "Your wallet has no credit left.",
                "next_step": "Top up your wallet, then repeat this request.",
            },
        )
    if not await admits(pool, ws.workspace_id, None, None):
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            {
                "code": "cost_cap_reached",
                "message": "Monthly cost cap reached.",
                "next_step": "Raise the cap or wait for the reset.",
            },
        )


async def store_integrations(
    pool: Any,
    ws_id: str,
    creds: dict[str, SecretStr],
    on_changed: Any,
    verify: Callable[..., Awaitable[Any]],
) -> tuple[list[dict], list[str], list[str]]:
    """Verify every provider in one pass, store each valid key, report every failure together.

    Returns ``(errors, warnings, stored_provider_names)``. A key whose provider cannot be reached is stored
    with a warning, as ``PUT /v1/integrations/{provider}`` does. Reasons never contain a key value.
    """
    names = list(creds)
    results = await asyncio.gather(*(verify(p, creds[p].get_secret_value()) for p in names))
    errors: list[dict] = []
    warnings: list[str] = []
    stored: list[str] = []
    for provider, res in zip(names, results, strict=True):
        if res.status == "credits_exhausted":
            errors.append(
                {
                    "provider": provider,
                    "code": "provider_quota_exhausted",
                    "message": res.reason or "Account has 0 lookup credits remaining",
                    "reason": res.reason or "Account has 0 lookup credits remaining",
                    "how_to_fix": "Top up credits in your provider dashboard, then repeat this request.",
                }
            )
            continue
        if res.status == "invalid_key":
            errors.append(
                {
                    "provider": provider,
                    "code": "invalid_provider_key",
                    "message": res.reason or f"Invalid API key for {provider}",
                    "reason": res.reason or f"Invalid API key for {provider}",
                    "how_to_fix": "Check the key in your provider dashboard and send a valid active key "
                    "under the same name, then repeat this request.",
                }
            )
            continue
        try:
            await upsert_credential(
                pool, ws_id, provider, "default", {"api_key": creds[provider].get_secret_value()}
            )
        except ValueError as exc:  # e.g. VAULT_KEK is not configured
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
        stored.append(provider)
        if res.status == "provider_unreachable":
            warnings.append(f"{provider}: could not be reached to verify; saved anyway.")
    if stored and on_changed is not None:
        await on_changed()
    return errors, warnings, stored


async def compute_blockers(pool: Any, cfg: Any, ws_id: str, agent_row: Any) -> list[dict]:
    from ..pack_catalog import list_variants, readiness_block, variant_readiness

    profile = agent_row["profile_name"]
    packs = list(agent_row["packs"]) if agent_row["packs"] is not None else None
    profiles_root = workspace_profiles_root(ws_id, cfg.repo_root)
    configured = await get_workspace_configured_providers(pool, ws_id)
    out: list[dict] = []
    try:
        variants = list_variants(cfg.repo_root, profiles_root, profile)
    except (ValueError, OSError):
        return [
            {
                "pack": "*",
                "variant": "*",
                "kind": "profile",
                "name": profile,
                "what_is_missing": "the profile's packs could not be read",
                "how_to_fix": "Re-run onboarding for this profile, then repeat this request.",
            }
        ]
    for resolved in variants:
        pack = resolved.graph.pack
        if packs is not None and pack not in packs:
            continue
        try:
            report = variant_readiness(
                profiles_root, profile, resolved, configured_integrations=configured
            )
        except ValueError:
            out.append(
                {
                    "pack": pack,
                    "variant": resolved.graph.variant,
                    "kind": "profile",
                    "name": profile,
                    "what_is_missing": "the profile files are not provisioned yet",
                    "how_to_fix": "Run onboarding for this profile, then repeat this request.",
                }
            )
            continue
        for item in readiness_block(report, resolved, all_items=False)["items"]:
            if item["status"] != "blocked":
                continue
            out.append(
                {
                    "pack": pack,
                    "variant": resolved.graph.variant,
                    "kind": item["kind"],
                    "name": item["name"],
                    "what_is_missing": item.get("reason")
                    or f"{item['kind']} '{item['name']}' is missing",
                    "how_to_fix": _how_to_fix(item["kind"], item["name"]),
                }
            )
    return out
