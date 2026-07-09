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
    version="0.4.0",
    phase="5",
    requires_capability=("solution-architecture",),
    description='Turn a use case and requirements into a solution architecture — either mapped onto the active company\'s flagship product (Mode A, product-led) or synthesised as a bespoke custom build (Mode B). Produces: requirements confirmation, feasibility assessment with V1/V2 cut, architecture, Mermaid diagrams, and a full design doc. Trigger when the user says "design the solution for [company]", "draft an architecture for [company]", "build a solution design / SAD for [company]", "create architecture diagrams for [company]", "map the gateway architecture for [use case]", "current and target state for [company]", or "bespoke build plan for [company]". Consumes a `solution-discovery` dossier when present. Read-only; produces files in `content/<active>/accounts/<account-slug>/`, and can hand off to `build-deck` (slides) or the docx skill (Word).',
)
