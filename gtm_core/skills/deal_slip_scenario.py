"""Canonical manifest for the `deal-slip-scenario` skill.

Prompt body: plugin/skills/deal-slip-scenario/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="deal-slip-scenario",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="3D",
    description=(
        'Model the financial impact on quota if a specific high-value deal slips to next quarter, shrinks, or is lost, computing gap-to-goal and mitigation paths. Trigger when the user says "what if [deal/company] slips", "model deal slip scenario for [account]", "deal risk scenario", or "pipeline gap analysis".'
    ),
)
