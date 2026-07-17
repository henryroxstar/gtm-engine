"""Canonical manifest for the `setup` skill (scaffolded in Phase A).

Prompt body: plugin/skills/setup/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="setup",
    capability_tier=Tier.CORE,
    version="0.4.0",
    phase="1",
    description='Guided one-time onboarding for the GTM engine plugin. This skill should be used when the user says "set me up", "set up gtm-engine", "onboard me", "configure my GTM profile", "get me started", or is running the plugin for the first time. Interviews the colleague, scaffolds their company profile bundle, walks them through connecting optional tools (Vibe Prospecting, Firecrawl) without ever storing a key in a file, and runs a 3-account proof.',
)
