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
    version="0.5.0",
    phase="1",
    description='Guided one-time onboarding for the GTM engine plugin. This skill should be used when the user says "set me up", "set up gtm-engine", "onboard me", "configure my GTM profile", "get me started", or is running the plugin for the first time. Learns the company from their own site (or whatever they hand over), asks only the gaps a website can\'t answer, stages a full profile bundle for the founder to review before anything goes live, walks them through connecting optional tools (Vibe Prospecting, Firecrawl, Higgsfield) without ever storing a key in a file, and proves value with a real first output: extract → review → promote → first output.',
)
