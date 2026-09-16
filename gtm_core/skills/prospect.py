"""Canonical manifest for the `prospect` skill (scaffolded in Phase A).

Prompt body: plugin/skills/prospect/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="prospect",
    capability_tier=Tier.CORE,
    version="0.13.0",
    phase="1",
    description=(
        'Discover, score, and qualify high-fit target accounts and buyers against ICP criteria using research waterfalls. Trigger when the user says "prospect for accounts", "find buyers at [company]", "build prospect list for [industry]", "qualify leads", or "run prospecting sweep".'
    ),
)
