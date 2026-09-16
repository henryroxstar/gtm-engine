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
    description=(
        'Automate weekly carousel production from market scan signals to finished on-brand slide decks and visuals. Trigger when the user says "auto-carousel", "weekly carousel", "carousel from market scan", "what should I carousel this week", or "build a carousel from the scan".'
    ),
)
