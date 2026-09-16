"""Canonical manifest for the `product-partner-brief` skill.

Prompt body: plugin/skills/product-partner-brief/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="product-partner-brief",
    capability_tier=Tier.CORE,
    version="0.2.0",
    phase="4",
    description=(
        'Draft a strategic product integration and tech-partner co-selling brief outlining mutual architecture and commercial value. Trigger when the user says "build product partner brief for [partner]", "tech partner one-pager", "integration brief", or "co-sell guide for [company]".'
    ),
)
