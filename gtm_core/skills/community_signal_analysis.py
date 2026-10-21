"""Canonical manifest for the `community-signal-analysis` skill.

The prompt body lives verbatim in
plugin/skills/community-signal-analysis/body_template.md; the generated SKILL.md is
produced from this manifest + that body by gtm_core.skills.codegen (never hand-edited).

Generic + company-agnostic: the skill names no vendor or product — the taxonomy comes
from the active profile's own knowledge and from the Syften account's configured filters.
Pull + read are via the read-only agent/mcp/syften wrapper; the deterministic scoring +
HTML render live in gtm_core/community_signal. No metered per-call unit (Syften is a flat
subscription) → Tier.CORE.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="community-signal-analysis",
    capability_tier=Tier.CORE,
    version="0.2.0",
    description=(
        'Analyze community social-listening feeds from Syften to track share-of-voice, signal momentum, and render an HTML dashboard. Trigger when the user says "community signal", "social listening", "run the Syften analysis", "what is the community saying", "check the listening feed", or "tune my Syften filters".'
    ),
    fallback_note=(
        "If the Syften connector is not configured (no `SYFTEN_API_KEY` — the `mcp__syften__*` "
        "tools are absent), fall back to a **manual CSV/JSON drop**: ask the operator to export "
        "the matches from the Syften dashboard and place the file at "
        "`content/<active>/community-signals/raw/pull-<date>.<csv|json>`. Then run the same "
        "deterministic pipeline on that file — `python -m gtm_core.community_signal.score` for "
        "the metrics and `python -m gtm_core.community_signal.render` for the HTML. Signal-quality "
        "scoring still works as long as the export carries Syften's AI accept/reject verdict "
        "column; filter suggestions remain recommend-only regardless of connector state."
    ),
)
