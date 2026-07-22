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
        "Weekly agentic-AI market signals sweep for the active company's GTM. "
        "Scans news, competitor moves, regulatory bodies, and standards activity; "
        "rates signals by strength (H / M / L); and produces a dated brief with "
        "ready-to-use LinkedIn posts, a campaign idea, a blog brief, a carousel "
        "concept, and a technical POC flag. Reads target_markets and language from "
        "the colleague's PROFILE, and the competitor / regulator / pillar config from "
        "the profile knowledge pack — never hardcodes any geography or company. "
        "Demand-driven: auto-discovers the colleague's own GTM plan, email sequence, "
        "account plan, or campaign when present and focuses the sweep on the "
        "industries, use-case clusters, personas, and geos where direct sales is "
        "actually running, tagging every signal by cohort / persona / use-case and an "
        "On-focus / Adjacent / Off-focus score. All sources are free (web search, "
        "browser) — no metered calls, no budget impact. This skill should be used when "
        'the user says "run my market scan", "weekly market scan", "what\'s moving in '
        'the market this week", "scan for market signals", "what should I be posting '
        'about", "catch me up on agentic AI", "content ideas", or on the Monday '
        "weekly cadence."
    ),
)
