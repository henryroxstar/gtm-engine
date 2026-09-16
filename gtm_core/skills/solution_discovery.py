"""Canonical manifest for the `solution-discovery` skill (scaffolded in Phase A).

Prompt body: plugin/skills/solution-discovery/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="solution-discovery",
    capability_tier=Tier.CORE,
    version="0.1.3",
    phase="5",
    requires_capability=("technical-discovery",),
    description=(
        'Profile an account\'s engineering stack and requirements to produce a discovery brief and decision-mapped question bank for deep-dives. Trigger when the user says "prep me for the technical deep-dive with [company]", "solution discovery for [company]", "what technical questions should I ask [company]", "profile [company]\'s stack", or "surface requirements for [company]".'
    ),
)
