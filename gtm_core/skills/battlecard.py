"""Canonical manifest for the `battlecard` skill.

Prompt body: plugin/skills/battlecard/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
Product-agnostic (no requires_capability): every fact it states comes from the active
profile, so it works for any tenant and for either a product-led or a bespoke engagement.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="battlecard",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="6",
    description=(
        'Build a competitor battlecard from the profile\'s competitive-positioning pack: where we win, where they win, and the trap questions. Trigger when the user says "battlecard for [competitor]", "how do we compete with [X]", or "competitive positioning against [vendor]".'
    ),
)
