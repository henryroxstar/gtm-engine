"""Canonical manifest for the `content-radar` skill (scaffolded in Phase A).

Prompt body: plugin/skills/content-radar/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="content-radar",
    capability_tier=Tier.PIPELINE,
    version="0.2.0",
    phase="1",
    description=(
        'Scan news and discovery items, cluster stories by content pillars, rank angles, and generate dated digests and candidate hooks. Trigger when the user says "run content radar", "what\'s trending for content", "scan the news for content", "what should we post about", "content radar", or "refresh the radar".'
    ),
)
