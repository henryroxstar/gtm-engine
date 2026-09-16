"""Canonical manifest for the `carousel-pdf` skill (scaffolded in Phase A).

Prompt body: plugin/skills/carousel-pdf/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="carousel-pdf",
    capability_tier=Tier.CORE,
    version="0.5.0",
    phase="2B",
    license="MIT",
    description=(
        'Produce an on-brand LinkedIn portrait carousel PDF and slide PNGs with caption variants from knowledge pack topics. Trigger when the user says "make a carousel about [topic]", "turn this into a carousel", "lead-magnet PDF for [topic]", "make a myth-bust carousel", or "make a how-to carousel".'
    ),
)
