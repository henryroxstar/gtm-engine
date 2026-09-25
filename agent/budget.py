"""VPS pre-run budget guard — the single home for the monthly-cap check the
headless ``PipelineRunner`` calls before any paid stage.

Centralised on the shared :func:`gtm_core.metering.check_budget` contract so
there is one cap source (``settings.json:monthly_budget_usd``, default $25)
and one fail policy. Fail-open on a read error: the SDK per-run ``max_budget_usd``
(``Config.per_run_cap_usd``) is the hard backstop for the unreadable case.
"""

from __future__ import annotations

from typing import Any

from gtm_core.metering import JsonlSink, check_budget

_DEFAULT_MONTHLY_BUDGET_USD = 25.0
# Defense-in-depth absolute ceiling: deny even if the cap is misconfigured huge.
_HARD_CEILING_MULT = 2.0


def monthly_cap_usd(cfg: Any, profile: str) -> float:
    """Read monthly tool cap from PROFILE.md via budget_status (default $50)."""
    from gtm_core import budget_status

    p_root = getattr(cfg, "profiles_root", None)
    c_root = getattr(cfg, "content_root", None)
    return budget_status._get_cap(profile, profiles_root=p_root, content_root=c_root)


def vps_budget_ok(cfg: Any, profile: str) -> bool:
    """True if the profile is under its monthly cost cap (else the run must abort).

    Deny at/over the cap; also deny over a 2x hard ceiling. Fail-open on a ledger
    read error — the per-run SDK cap backstops the unreadable case.
    """
    cap = monthly_cap_usd(cfg, profile)
    return check_budget(
        profile,
        cap,
        sink=JsonlSink(cfg, profile),
        hard_ceiling_usd=cap * _HARD_CEILING_MULT,
    )
