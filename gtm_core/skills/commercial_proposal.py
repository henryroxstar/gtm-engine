"""Canonical manifest for the `commercial-proposal` skill.

Prompt body: plugin/skills/commercial-proposal/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="commercial-proposal",
    capability_tier=Tier.CORE,
    version="0.1.0",
    description=(
        'Draft a customized commercial proposal, pricing model, and business terms deck for enterprise prospects. Trigger when the user says "draft commercial proposal for [company]", "create pricing proposal", "make a commercial offer", or "build proposal deck".'
    ),
)
