# gtm_core/skills/builder_radar.py
"""Canonical manifest for the `builder-radar` skill (renamed from `journey-radar`;
internal state paths and the history `source:"journey"` tag are unchanged).

Prompt body: plugin/skills/builder-radar/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="builder-radar",
    capability_tier=Tier.CORE,
    version="0.3.0",
    phase="journey-m1",
    description=(
        'Scan repo git history and project design docs to surface story-worthy build milestones and generate dated digests and story clusters. Trigger when the user says "run builder radar", "run journey radar", "scan build history", "what have we shipped that is worth a story", or on the weekly builder cadence.'
    ),
)
