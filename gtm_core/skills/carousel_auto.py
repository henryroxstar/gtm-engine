"""Canonical manifest for the `carousel-auto` skill (scaffolded in Phase A).

Prompt body: plugin/skills/carousel-auto/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="carousel-auto",
    capability_tier=Tier.PRODUCTION,
    version="0.5.0",
    phase="4C",
    license="MIT",
    description='Automate the weekly carousel pipeline from market-scan signals to publish-ready package. This skill should be used when the user says "auto-carousel", "run my carousel workflow", "weekly carousel", "carousel from market scan", "carousel from this week\'s scan", "generate this week\'s carousel", "automate my carousel", "what should I carousel this week", "build a carousel from the scan", or "turn this week\'s signal into a carousel". Reads the latest market-signals file, scores signals for carousel potential, picks the strongest arc shape and theme, chains through carousel-pdf to render the deck, then optionally chains to carousel-visuals for cover art and motion teaser — all in one guided run.',
)
