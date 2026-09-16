"""Canonical manifest for the `video-score` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/video-score/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-score",
    capability_tier=Tier.PIPELINE,
    version="0.6.0",
    phase="6",
    description=(
        'Score short-form video variants on retention and virality to recommend optimal cuts and platform destinations. Trigger when the user says "score the renders", "which cut should we post", "check virality", "rank the variants", or as the score stage of the creator pack.'
    ),
)
