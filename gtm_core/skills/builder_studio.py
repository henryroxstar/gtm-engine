# gtm_core/skills/builder_studio.py
"""Canonical manifest for the `builder-studio` skill (renamed from
`journey-studio`; internal state paths and the history `source:"journey"` tag
are unchanged).

Prompt body: plugin/skills/builder-studio/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="builder-studio",
    capability_tier=Tier.CORE,
    version="0.5.0",
    phase="journey-m2",
    description=(
        'Draft an authentic, developer-voiced builder story across LinkedIn and X formats grounded in primary git commits and evidence packs. Trigger when the user says "write the builder story", "draft the journey post", "turn this milestone into content", or after builder evidence approval.'
    ),
)
