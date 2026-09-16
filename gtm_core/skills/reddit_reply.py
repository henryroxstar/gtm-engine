"""Canonical manifest for the `reddit-reply` skill (engagement cohort, phase 3C).

Prompt body: plugin/skills/reddit-reply/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="reddit-reply",
    capability_tier=Tier.CORE,
    version="0.1.1",
    phase="3C",
    description=(
        'Draft helpful, non-promotional Reddit comments that directly address technical questions while subtly citing relevant experience. Trigger when the user says "reply to this reddit thread", "draft reddit comment", "respond to this discussion", or provides a Reddit thread URL.'
    ),
)
