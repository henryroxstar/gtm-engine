"""Canonical manifest for the `creator-brief` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/creator-brief/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="creator-brief",
    capability_tier=Tier.PIPELINE,
    version="0.5.0",
    phase="6",
    description=(
        'Brief stage of the video pipeline (routed via video-router): plans a short-form video or product film concept into an approved brief.json, preset selection, creative brief, and beat sheet before script generation. Trigger when the user says "write a creator brief", "creative brief for [concept]", or as the brief stage of the video pack.'
    ),
)
