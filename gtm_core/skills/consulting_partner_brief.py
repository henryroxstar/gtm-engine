"""Canonical manifest for the `consulting-partner-brief` skill.

Prompt body: plugin/skills/consulting-partner-brief/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="consulting-partner-brief",
    capability_tier=Tier.CORE,
    version="0.2.0",
    phase="4",
    description=(
        'Build an executive joint-value and enablement brief for systems integrators and consulting partners. Trigger when the user says "build consulting partner brief for [partner]", "SI partner deck", "joint solution brief", or "enablement guide for [firm]".'
    ),
)
