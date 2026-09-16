"""Canonical manifest for the `infographic-handwritten` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/infographic-handwritten/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="infographic-handwritten",
    capability_tier=Tier.PRODUCTION,
    version="0.3.0",
    phase="3B",
    description=(
        'Render handwritten-style notebook, formula, or whiteboard infographics on paper texture using Higgsfield. Trigger when the user says "make a handwritten infographic", "whiteboard-style graphic", "notebook sketch of [framework]", or "hand-drawn visual".'
    ),
)
