# gtm_core/skills/video_restyle.py
"""Canonical manifest for the `video-restyle` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/video-restyle/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-restyle",
    capability_tier=Tier.PRODUCTION,
    version="0.1.0",
    phase="11",
    description=(
        'Apply trained brand styles, palettes, and visual presets to real footage using Higgsfield Shorts Studio. Trigger when the user says "restyle this video", "apply brand look to footage", "restyle shorts", or as the restyle stage of the creator pack.'
    ),
)
