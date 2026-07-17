"""Canonical manifest for the `call-prep` skill (scaffolded in Phase A).

Prompt body: plugin/skills/call-prep/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="call-prep",
    capability_tier=Tier.CORE,
    version="0.3.0",
    phase="3",
    description='Prepare a pre-meeting brief for a sales call — account snapshot, attendee persona mapping, matched case study, likely objections with rebuttals, sharp discovery questions, and a clear ask. This skill should be used when the user says "prep me for my call with [company]", "I\'m meeting with [company] prep me", "call prep for [person]", "what should I know before talking to [company]", "prepare for my meeting with [X]", or "brief me on [company] before my call". Reads PROFILE for markets and ICP weighting. Produces a one-page brief saved to the account folder (`content/<active>/accounts/<account-slug>/`).',
)
