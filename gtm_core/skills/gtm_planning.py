"""Canonical manifest for the `gtm-planning` skill (scaffolded in Phase A).

Prompt body: plugin/skills/gtm-planning/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="gtm-planning",
    capability_tier=Tier.CORE,
    version="0.4.0",
    phase="4",
    description='Build or refresh the quarterly GTM plan for the colleague\'s market. This skill should be used when the user says "build my quarterly plan", "refresh my GTM plan", "what\'s my focus this quarter", "plan my quarter", "update the GTM plan", "quarterly planning", "write the plan for Q[N]", "what should I prioritise this quarter", or "help me plan my GTM motion". Reads PROFILE for market, ICP weighting, and targets. Produces a structured, written plan the colleague can share with their manager or regional team.',
)
