"""Canonical manifest for the `market-scan` skill (first migration, Phase A).

The prompt body lives verbatim in plugin/skills/market-scan/body_template.md;
plugin/skills/market-scan/SKILL.md is generated from this manifest + that body by
gtm_core.skills.codegen. market-scan runs entirely on free web tools (no metered
calls, no server-side connectors) → Tier.CORE.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="market-scan",
    capability_tier=Tier.CORE,
    version="0.3.1",
    phase="2",
    description=(
        'Sweep weekly news, competitor moves, and standards to produce a dated market signal brief, post drafts, and campaign ideas. Trigger when the user says "run my market scan", "weekly market scan", "what\'s moving in the market this week", "scan for market signals", "what should I be posting about", or "content ideas".'
    ),
)
