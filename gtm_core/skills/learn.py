"""Canonical manifest for the `learn` skill.

SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="learn",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="UX",
    description=(
        'Learn from new company materials, decks, or transcripts, showing exact diffs and asking for approval before updating profile knowledge. Trigger when the user says "learn from my new material", "learn from this doc", "learn from this deck", "update knowledge from this", or "intake new material".'
    ),
)
