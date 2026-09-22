"""Canonical manifest for the `team-pipeline` skill.

Prompt body: plugin/skills/team-pipeline/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="team-pipeline",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="3D",
    description=(
        'Produce a sales leadership view of team pipeline aggregated by rep, stage, and risk profile, highlighting coaching moments and coverage ratios. Trigger when the user says "review team pipeline", "leader view of pipeline", "rep pipeline coverage", or "1:1 pipeline review".'
    ),
)
