"""Canonical manifest for the `video-plan` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/video-plan/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-plan",
    capability_tier=Tier.PIPELINE,
    version="1.0.0",
    phase="6",
    description=(
        'Plan and route video concepts into approved presets, preflights, and brief.json before script generation. Trigger when the user says "plan a video", "make a video", "create a video", "video plan", "which video lane", or as the plan stage of the creator pack.'
    ),
)
