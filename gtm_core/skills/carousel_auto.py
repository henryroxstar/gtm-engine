"""Canonical manifest for the `carousel-auto` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/carousel-auto/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="carousel-auto",
    capability_tier=Tier.PRODUCTION,
    version="0.6.0",
    phase="4C",
    license="MIT",
    description='Automate the weekly carousel pipeline from market-scan signals to publish-ready package. This skill should be used when the user says "auto-carousel", "run my carousel workflow", "weekly carousel", "carousel from market scan", "carousel from this week\'s scan", "generate this week\'s carousel", "automate my carousel", "what should I carousel this week", "build a carousel from the scan", or "turn this week\'s signal into a carousel". Reads the latest market-signals file, scores signals for carousel potential, picks the strongest arc shape and theme, chains through carousel-pdf to render the deck, then optionally chains to carousel-visuals for cover art and motion teaser — all in one guided run.',
)
