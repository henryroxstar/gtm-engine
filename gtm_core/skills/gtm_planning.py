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
    description=(
        'Build or refresh a quarterly GTM plan with target segments, ICP weighting, and motion milestones for the active market. Trigger when the user says "build my quarterly plan", "refresh my GTM plan", "what\'s my focus this quarter", "plan my quarter", "write the plan for Q[N]", or "help me plan my GTM motion".'
    ),
)
