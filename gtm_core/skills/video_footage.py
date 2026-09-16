"""Canonical manifest for the `video-footage` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/video-footage/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-footage",
    capability_tier=Tier.PRODUCTION,
    version="1.0.0",
    phase="10",
    description=(
        'Ingest, clip, reframe, and restyle existing video footage, screen recordings, and demo assets. Trigger when the user says "clip my video", "repurpose this recording", "make shorts from this video", "restyle this footage", or as the footage stage of creator pack.'
    ),
)
