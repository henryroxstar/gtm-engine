"""Canonical manifest for the `diagram-design` skill.

Prompt body: plugin/skills/diagram-design/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="diagram-design",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="3D",
    description=(
        'Create publication-grade, on-brand architectural SVGs, flowcharts, MEDDPICC scorecards, quadrant matrices, and HTML diagram companions from Mermaid, Excalidraw, or concept specs using native editorial rendering. Trigger when the user says "design diagram", "render architecture diagram", "generate SVG schematic", "convert mermaid to SVG", or "make diagram for [topic]".'
    ),
)
