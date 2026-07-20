"""Canonical manifest for the `prospect` skill (scaffolded in Phase A).

Prompt body: plugin/skills/prospect/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="prospect",
    capability_tier=Tier.CORE,
    version="0.4.0",
    phase="1",
    description='Run the active profile\'s prospecting routine — discover, qualify, score, and enrich ICP accounts, then output a scored brief, Tier-A outreach packs, and a HubSpot-ready CSV. Product-agnostic: markets, ICP, and the lead product all come from whichever profile is active. Use when the user says "run my prospecting", "find prospects", "build a prospect list", "weekly prospecting run", "find accounts for [the lead product]", or "prospect [market]". Uses two data sources when connected — Vibe Prospecting (discovery + firmographics + Bombora topic-intent + events) and RocketReach (contact resolution + Intentsify topic-intent + news/hiring signals + job-change timing); free web-search is the fallback. Respects budget caps before any metered call.',
)
