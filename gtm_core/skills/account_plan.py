"""Canonical manifest for the `account-plan` skill (scaffolded in Phase A).

Prompt body: plugin/skills/account-plan/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="account-plan",
    capability_tier=Tier.CORE,
    version="0.4.0",
    phase="3",
    description=(
        'Build a strategic CRO-grade account plan with MEDDPICC scorecard, buying influence map, value hypothesis, and mutual action plan. Trigger when the user says "build an account plan for [company]", "strategic plan for [account]", "plan my approach to [company]", or "how do I land [company]".'
    ),
)
