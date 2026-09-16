"""Canonical manifest for the `airq-scan` skill.

Prompt body: plugin/skills/airq-scan/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="airq-scan",
    capability_tier=Tier.PRODUCTION,
    version="0.1.1",
    phase="3D",
    requires_capability=("gateway",),
    description=(
        'Run an AIRQ-aligned agent-security assessment of a target company\'s AI agent product, producing two LinkedIn-ready infographics and give-first outreach copy. Trigger when the user says "run an AIRQ scan on [URL]", "assess [company] AI agent risk", "AIRQ audit [URL/screenshot]", or "score [company]\'s agent security".'
    ),
)
