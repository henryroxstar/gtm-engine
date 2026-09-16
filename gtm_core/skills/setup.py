"""Canonical manifest for the `setup` skill (scaffolded in Phase A).

Prompt body: plugin/skills/setup/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="setup",
    capability_tier=Tier.CORE,
    version="0.6.0",
    phase="1",
    description=(
        'Guided onboarding for the GTM engine, learning company context, configuring optional integrations, and generating a first output. Trigger when the user says "set me up", "set up gtm-engine", "onboard me", "configure my GTM profile", or "get me started".'
    ),
)
