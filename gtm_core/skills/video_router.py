"""Canonical manifest for the `video-router` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/video-router/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-router",
    capability_tier=Tier.CORE,
    version="0.8.0",
    phase="6",
    description=(
        'Single front-door orchestrator for all video creation and clipping: analyzes requirements, existing footage, and engine readiness to route into the optimal production lane. Trigger when the user says "make a video", "create a video", "turn this into a short", "I want a reel", "make a demo clip", "clip my video", "repurpose this recording", "make a product film", or "which video lane should I use".'
    ),
)
