"""Canonical manifest for the `video-avatar` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/video-avatar/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-avatar",
    capability_tier=Tier.PRODUCTION,
    version="0.6.0",
    phase="P4",
    description=(
        'Render speaking presenter beats as synthetic talking heads on HeyGen with approved avatar looks, voices, and disclosures. Trigger when the user says "render avatar video", "create talking head", "render presenter beats", or as the presenter stage of the creator pack.'
    ),
)
