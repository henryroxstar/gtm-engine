"""Canonical manifest for the `deck-research` skill (scaffolded in Phase A).

Prompt body: plugin/skills/deck-research/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="deck-research",
    capability_tier=Tier.CORE,
    version="0.1.1",
    phase="4",
    description=(
        'Research a target account into a structured deck dossier with persona slot-fills to feed presentation generation. Trigger when the user says "research [company] for a deck", "build a deck dossier for [company]", "get me deck research on [company]", "deep research [company] for slides", or "prep deck research for [persona] at [company]".'
    ),
)
