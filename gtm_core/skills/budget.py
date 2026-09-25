"""Canonical manifest for the `budget` skill.

SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="budget",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="UX",
    description=(
        'Report current month tool spend and monthly budget cap in one plain sentence. Trigger when the user says "what\'s my budget", "how much have we spent", "budget", "cost", or "check budget".'
    ),
)
