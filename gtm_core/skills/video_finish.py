"""Canonical manifest for the `video-finish` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/video-finish/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-finish",
    capability_tier=Tier.PIPELINE,
    version="0.7.0",
    phase="A",
    description=(
        'Finishing stage of the video pipeline (routed via video-router): stitches rendered video beats or builds screen_ui HTML motion scenes, burns timed captions, mixes audio beds, and applies transitions. Trigger when the user says "finish the video", "burn captions", "stitch video clips", "mix video audio", or as the finish stage of the video pack.'
    ),
)
