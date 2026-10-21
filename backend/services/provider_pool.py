"""A workspace's allocated share of a pooled provider key (MM4).

The allocation itself is ``allocate_provider_share`` in Postgres (V045): it takes the lock that
makes ``sum(shares) <= pool`` true by construction. This module only asks it, for the workspace a run
belongs to, and turns whatever happens into integers a worker can enforce.

Absence refuses. A database that cannot answer, an entitlement the registry does not name, a pool the
operator never sized: each is a share of ZERO, and a house worker with a zero share refuses every
metered call. Nothing here ever guesses a share.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from gtm_core.provider_registry import default_share

from ..database import workspace_scope

_log = logging.getLogger(__name__)


async def resolve_provider_shares(
    pool, workspace_id: str, providers: Iterable[str]
) -> dict[str, int]:
    """``{provider: units}`` this workspace may spend of each pooled provider's key this month."""
    providers = tuple(providers)
    try:
        async with workspace_scope(pool, workspace_id) as conn:
            entitlement = await conn.fetchval(
                "SELECT entitlement FROM subscriptions WHERE workspace_id = $1::uuid", workspace_id
            )
            out: dict[str, int] = {}
            for provider in providers:
                granted = await conn.fetchval(
                    "SELECT allocate_provider_share($1::uuid, $2, $3)",
                    workspace_id,
                    provider,
                    default_share(provider, entitlement),
                )
                out[provider] = max(0, int(granted or 0))
            return out
    except Exception:  # noqa: BLE001 — an unanswerable allocation is no allocation
        _log.warning(
            "provider share for workspace %s could not be resolved; treating every share as zero",
            workspace_id,
            exc_info=True,
        )
        return dict.fromkeys(providers, 0)
