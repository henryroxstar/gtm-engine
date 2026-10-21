# gtm_core/skills/builder_evidence.py
"""Canonical manifest for the `builder-evidence` skill (renamed from
`journey-evidence`; internal state paths and the history `source:"journey"` tag
are unchanged).

Prompt body: plugin/skills/builder-evidence/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="builder-evidence",
    capability_tier=Tier.CORE,
    version="0.4.0",
    phase="journey-m2",
    description=(
        'Assemble a primary-source evidence pack from git commits, diffs, and design docs for a builder-story moment. Trigger when the user says "gather evidence for this moment", "pull the commits for this story", "research this build moment", or after Gate 1 plan approval for a builder item.'
    ),
)
