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
    version="0.5.0",
    phase="5",
    requires_capability=("solution-architecture",),
    description='Turn a use case and requirements into a solution architecture — either mapped onto the active company\'s flagship product (Mode A, product-led) or synthesised as a bespoke custom build (Mode B). Produces a customer-facing **Solution Overview**: a ½-page executive summary + a scannable customer overview (problem & why-now, the solution, how the product works, current→target architecture, how-it-works, V1/V2 cut, talking points/FAQ), with the SA rigor (feasibility, component inventory, standards crosswalk, shared-responsibility, trade-offs) kept in a droppable technical appendix — delivered as Markdown + a polished, readable HTML companion. Trigger when the user says "design the solution for [company]", "draft an architecture for [company]", "build a solution design / SAD for [company]", "create architecture diagrams for [company]", "map the gateway architecture for [use case]", "current and target state for [company]", or "bespoke build plan for [company]". Consumes a `solution-discovery` dossier when present. Read-only; produces files in `content/<active>/accounts/<account-slug>/`, and can hand off to `build-deck` (slides) or the docx skill (Word).',
)
