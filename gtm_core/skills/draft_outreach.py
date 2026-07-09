"""Canonical manifest for the `draft-outreach` skill (scaffolded in Phase A).

Prompt body: plugin/skills/draft-outreach/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="draft-outreach",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="1",
    description='Draft outreach for the active company\'s flagship product — LinkedIn DMs, cold emails, and follow-ups — in the colleague\'s voice, built from a real "why now" signal, the hook matrix, and the matched case study. This skill should be used when the user says "draft outreach to [person/company]", "write a cold email to [prospect]", "write a LinkedIn DM to [name]", "reach out to [name] at [company]", or "refine this outreach". Reads brand, signature, voice, and language from the active profile. Produces drafts only — never sends.',
)
