"""Canonical manifest for the `security-review` skill.

Prompt body: plugin/skills/security-review/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
Product-agnostic (no requires_capability): every fact it states comes from the active
profile, so it works for any tenant and for either a product-led or a bespoke engagement.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="security-review",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="5",
    description=(
        'Answer a customer security questionnaire or vendor assessment from the profile\'s evidence pack, refusing any question no entry backs. Trigger when the user says "answer this security questionnaire", "security review for [company]", "fill out the vendor assessment", or "respond to their DDQ".'
    ),
)
