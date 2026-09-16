"""Canonical manifest for the `gateway-runbook` skill (scaffolded in Phase A).

Prompt body: plugin/skills/gateway-runbook/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="gateway-runbook",
    capability_tier=Tier.CORE,
    version="0.3.1",
    phase="5",
    requires_capability=("gateway",),
    # Tenant boundary: the product's own setup concepts, click-paths, and API routes live in
    # the profile's product reference pack, not here — the description stays category-generic.
    description=(
        'Generate a step-by-step gateway setup runbook tailored to a specific account, pattern, and engineering stack with validation checklists. Trigger when the user says "write the gateway setup runbook for [company]", "gateway setup steps for [use case]", "implementation runbook for [company]", "how do we set up the gateway for [pattern]", or "deployment runbook for [company]".'
    ),
)
