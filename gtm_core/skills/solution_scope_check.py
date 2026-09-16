"""Canonical manifest for the `solution-scope-check` skill.

Prompt body: plugin/skills/solution-scope-check/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
Product-agnostic (no requires_capability): works for any profile and for either
Mode-A (product-led) or Mode-B (bespoke) solution designs.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="solution-scope-check",
    capability_tier=Tier.CORE,
    version="0.2.0",
    phase="5",
    description=(
        'Evaluate project technical scope, feasibility, resource requirements, and risk boundaries before commercial commitment. Trigger when the user says "check solution scope for [company]", "scope check", "evaluate project feasibility", or "review technical delivery risk".'
    ),
)
