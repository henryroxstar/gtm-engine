"""Canonical manifest for the `poc-plan` skill.

Prompt body: plugin/skills/poc-plan/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
Product-agnostic (no requires_capability): every fact it states comes from the active
profile, so it works for any tenant and for either a product-led or a bespoke engagement.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="poc-plan",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="5",
    description=(
        'Turn a proposed solution into a time-boxed proof of concept with pass/fail criteria, a named verifier per criterion, exit criteria, and a technical-win memo. Trigger when the user says "plan a POC for [company]", "pilot plan", "proof of concept scope", or "what would a trial look like for [account]".'
    ),
)
