"""Canonical manifest for the `solution-design` skill (scaffolded in Phase A).

Prompt body: plugin/skills/solution-design/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="solution-design",
    capability_tier=Tier.CORE,
    version="0.5.1",
    phase="5",
    requires_capability=("solution-architecture",),
    description=(
        'Turn technical requirements into an executive solution overview and architecture specification across Markdown and HTML deliverables. Trigger when the user says "design the solution for [company]", "draft an architecture for [company]", "build a solution design / SAD for [company]", "create architecture diagrams for [company]", or "bespoke build plan for [company]".'
    ),
)
