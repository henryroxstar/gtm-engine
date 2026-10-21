"""Canonical manifest for the `linkedin-reply` skill (Phase 3C).

Prompt body: plugin/skills/linkedin-reply/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="linkedin-reply",
    capability_tier=Tier.CORE,
    version="0.7.2",
    phase="3C",
    description=(
        'Draft value-first public comments and soft-sell connection notes to target LinkedIn posts in the brand voice. Trigger when the user says "reply to this LinkedIn post", "comment on this post", "draft a soft-sell reply", or "respond to this post / screenshot".'
    ),
)
