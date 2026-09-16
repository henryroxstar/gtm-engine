"""Canonical manifest for the `video-storyboard` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/video-storyboard/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-storyboard",
    capability_tier=Tier.PRODUCTION,
    version="0.9.0",
    phase="6",
    description=(
        'Generate operator-reviewable storyboard hero stills and reference frame compositions before video render spend. Trigger when the user says "storyboard this script", "show me video frames before render", "preview shot composition", or as the storyboard stage of creator pack.'
    ),
)
