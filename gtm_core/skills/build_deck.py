"""Canonical manifest for the `build-deck` skill (scaffolded in Phase A).

Prompt body: plugin/skills/build-deck/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="build-deck",
    capability_tier=Tier.CORE,
    version="0.6.0",
    phase="4",
    description='Build an on-brand sales deck, one-pager, POC proposal, or partner brief for the active company. Trigger when the user says "build a deck for [company]", "make slides for [persona]", "create a presentation about [topic]", "put together a deck for [meeting]", "build a one-pager for [use case]", "write up a POC proposal for [company]", "make a partner brief for [company]", or any similar request for a presentation-format deliverable. Automatically detects the primary persona and selects the matching template. Confirms outline before generating. Supports Mode A (pptx) and Mode B (Slidev / on-brand).',
)
