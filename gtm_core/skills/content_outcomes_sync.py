"""Canonical manifest for the `content-outcomes-sync` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/content-outcomes-sync/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="content-outcomes-sync",
    capability_tier=Tier.PIPELINE,
    version="0.9.0",
    phase="8",
    description=(
        'Sync published post performance from Buffer MCP and social channels into content outcome ledgers to calibrate predictor_band accuracy. Trigger when the user says "sync content outcomes", "how did our posts perform", "analyze content metrics", or "calibrate virality scores".'
    ),
)
