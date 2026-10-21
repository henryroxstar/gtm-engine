"""Canonical manifest for the `content-research` skill (scaffolded in Phase A).

Prompt body: plugin/skills/content-research/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="content-research",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="1",
    description=(
        'Research a planned content item into verifiable facts, quotes, counterpoints, and claims to avoid using web and research tools. Trigger when the user says "research this item", "research the content", "get facts for the post", "content research", or after a content plan is approved.'
    ),
)
