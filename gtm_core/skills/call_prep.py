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
    version="0.3.1",
    phase="3",
    description=(
        'Prepare a pre-meeting brief for a sales call with attendee mapping, matched case study, objections, discovery questions, and a clear ask. Trigger when the user says "prep me for my call with [company]", "call prep for [person]", "what should I know before talking to [company]", or "prepare for my meeting with [X]".'
    ),
)
