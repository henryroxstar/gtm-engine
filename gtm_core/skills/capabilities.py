"""Canonical manifest for the `capabilities` skill.

SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="capabilities",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="UX",
    description=(
        'Explain what the engine can do and what tools are currently connected in plain English. Trigger when the user says "what can you help me with", "what\'s connected", "what can you do", "help me", or "show capabilities".'
    ),
)
