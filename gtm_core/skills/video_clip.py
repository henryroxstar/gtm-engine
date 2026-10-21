# gtm_core/skills/video_clip.py
"""Canonical manifest for the `video-clip` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/video-clip/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-clip",
    capability_tier=Tier.PRODUCTION,
    version="0.4.0",
    phase="10",
    description=(
        'Clip stage of the repurpose-clips and live-action video lanes (routed via video-router): extracts short-form highlight clips from long-form or camera footage with captions and reframing via Reap. Trigger when the user says "run Reap clipper", "extract highlight clips", or as the clip stage of the video pack.'
    ),
)
