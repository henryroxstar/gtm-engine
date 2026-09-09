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
    description='Research an account into a structured, reusable deck dossier that build-deck consumes to fill the account-specific slots of any persona template. Trigger when the user says "research [company] for a deck", "build a deck dossier for [company]", "get me deck research on [company]", "deep research [company] for slides", "prep deck research for [persona] at [company]", or asks for account intelligence specifically to feed a presentation. Produces a two-layer dossier (persona-agnostic account intel + per-persona slot-fills) saved to the account folder (`content/<active>/accounts/<account-slug>/`). Free web paths by default; metered tools (Firecrawl / Vibe) only on explicit opt-in within PROFILE budget. Read-only research; never sends anything.',
)
