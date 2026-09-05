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
    description='Produce a parameterized, step-by-step gateway setup runbook tailored to a specific account, use case, and stack — grounded in the active product\'s verified reference pack (its real setup concepts, dashboard click-paths, and syntax). Trigger when the user says "write the gateway setup runbook for [company]", "gateway setup steps for [use case]", "implementation runbook for [company]", "how do we set up the gateway for [pattern]", "give [company] the setup guide", or "deployment runbook for [company]". Can produce either a dashboard click-through guide (default) or a headless / config-paste appendix (ready-to-paste config payloads + the product\'s management-API path) when asked for "API setup" or "headless setup". Consumes a `solution-design` dossier when present (chosen pattern + component inventory). Includes validation tests, troubleshooting, and a go-live checklist. Read-only authoring — it documents the steps, it does not provision anything itself.',
)
