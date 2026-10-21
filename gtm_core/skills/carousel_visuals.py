"""Canonical manifest for the `carousel-visuals` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/carousel-visuals/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="carousel-visuals",
    capability_tier=Tier.PRODUCTION,
    version="0.7.0",
    phase="3B",
    description=(
        'Generate AI visuals, cinematic cover art, background cards, and motion teaser videos for carousels using Higgsfield. Trigger when the user says "add visuals to my carousel", "generate cover art for the carousel", "make an image carousel", "create a motion teaser", or "make it visual".'
    ),
)
