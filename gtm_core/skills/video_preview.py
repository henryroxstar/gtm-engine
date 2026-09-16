"""Canonical manifest for the `video-preview` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/video-preview/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-preview",
    capability_tier=Tier.PRODUCTION,
    version="1.0.0",
    phase="6",
    description=(
        'Generate operator-reviewable storyboard hero stills, animatics, and unified preview cards before video render spend. Trigger when the user says "preview video", "storyboard this script", "show me video frames", or as the preview stage of creator pack.'
    ),
)
