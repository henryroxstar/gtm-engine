"""Canonical manifest for the `draft-outreach` skill (scaffolded in Phase A).

Prompt body: plugin/skills/draft-outreach/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="draft-outreach",
    capability_tier=Tier.CORE,
    version="0.6.0",
    phase="1",
    description=(
        'Draft personalized cold emails, LinkedIn DMs, and follow-ups in the company voice using signals, hook matrices, and case studies. Trigger when the user says "draft outreach to [person/company]", "write a cold email to [prospect]", "write a LinkedIn DM to [name]", "reach out to [name] at [company]", or "refine this outreach".'
    ),
)
