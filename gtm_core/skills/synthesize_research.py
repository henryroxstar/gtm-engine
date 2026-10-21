"""Canonical manifest for the `synthesize-research` skill.

Prompt body: plugin/skills/synthesize-research/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="synthesize-research",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="3D",
    description=(
        'Synthesize qualitative customer feedback, interview transcripts, and support issues into structured product insights, pain points, and feature requests. Trigger when the user says "synthesize research", "analyze user feedback", "customer research synthesis", or "voice of customer summary".'
    ),
)
