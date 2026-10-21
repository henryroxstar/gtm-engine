"""Canonical manifest for the `outcomes-sync` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/outcomes-sync/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="outcomes-sync",
    capability_tier=Tier.PIPELINE,
    version="0.2.0",
    phase="4",
    description=(
        'Sync campaign outreach results and publish engagement into outcome ledgers, distilling learnings to promote into knowledge packs. Trigger when the user says "sync outcomes", "how did the campaign do", "update learnings", "what\'s working", "close the loop", or "pull campaign results".'
    ),
)
