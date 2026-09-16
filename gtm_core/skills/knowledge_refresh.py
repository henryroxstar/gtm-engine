"""Canonical manifest for the `knowledge-refresh` skill (knowledge-lifecycle PRD, Phase 3b).

Prompt body: plugin/skills/knowledge-refresh/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="knowledge-refresh",
    capability_tier=Tier.PIPELINE,
    version="0.1.0",
    phase="3",
    description=(
        'Safely scan and refresh stale knowledge pack topics from web sources, staging candidate updates for human promotion. Trigger when the user says "refresh knowledge", "refresh the knowledge pack", "update stale knowledge", "what knowledge is due", or "run the knowledge refresh".'
    ),
)
