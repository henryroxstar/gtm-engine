"""Canonical manifest for the `value-case` skill.

Prompt body: plugin/skills/value-case/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
Product-agnostic (no requires_capability): every fact it states comes from the active
profile, so it works for any tenant and for either a product-led or a bespoke engagement.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="value-case",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="5",
    description=(
        'Build the quantified business case for a deal: the cost of the status quo, the modelled delta, and the assumption each number rests on. Trigger when the user says "build the value case for [company]", "ROI model for [account]", "quantify the business case", or "cost of inaction for [company]".'
    ),
)
