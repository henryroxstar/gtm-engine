"""Canonical manifest for the `content-studio` skill (scaffolded in Phase A).

Prompt body: plugin/skills/content-studio/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.

**Direct-response patterns (0.6.0, 2026-09-14).**
When `item.goal == "conversion"`, Step 1 loads `docs/direct-response-patterns.md`
to structure written copy around the 5 B2B desire frameworks and dual-action platform bridges.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="content-studio",
    capability_tier=Tier.CORE,
    version="0.6.0",
    phase="2",
    description=(
        'Draft platform-native social copy across LinkedIn, X, and Instagram matching company voice and approved content plans. Trigger when the user says "write the post", "draft content for [item]", "generate copy for the plan", or as the studio stage of the content pipeline.'
    ),
)
