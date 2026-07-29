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
    description='Produce a LinkedIn 4:5 portrait carousel (PDF document post) from the active company\'s knowledge pack. This skill should be used when the user says "make a carousel about [topic]", "turn this into a carousel", "lead-magnet PDF for [topic]", "make a myth-bust carousel", "make a how-to carousel", "make a case-study carousel", "make a framework carousel", "make a light carousel", or "post about [topic]". Takes a topic or signal → selects arc shape (myth-bust, how-to, case-study, framework, or default) → drafts the 8–10 card copy arc with swipe momentum → shows for approval → renders an on-brand PDF + per-slide PNGs → outputs caption (2 variants) and a close (compliant recap + single ask by default; opt-in trigger-word lead magnet with DM copy). Dark (default) or light theme per carousel.',
)
