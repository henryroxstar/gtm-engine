"""Canonical manifest for the `video-render` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/video-render/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-render",
    capability_tier=Tier.PRODUCTION,
    version="1.2.1",
    phase="6",
    description=(
        'B-roll render stage of the video pipeline (routed via video-router): renders b-roll and screen visuals from approved shot prompts and storyboard start images using video generation models. Trigger when the user says "render video shots", "generate b-roll video", "render visual beats", or as the render stage of the video pack.'
    ),
)
