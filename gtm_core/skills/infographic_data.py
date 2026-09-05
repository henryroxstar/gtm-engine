"""Canonical manifest for the `infographic-data` skill.

Prompt body: plugin/skills/infographic-data/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="infographic-data",
    capability_tier=Tier.PRODUCTION,
    version="0.3.0",
    phase="3B",
    description='Render a finished, postable data-dense editorial infographic — a single image with a bold headline, numbered sections, big stat anchors, and donut/bar/icon charts on the active company\'s brand palette — from a brief, topic, research pack, or source doc, using Higgsfield. Pins every number and label in an approved spec at a plan gate before any paid call, re-flows the layout per platform (LinkedIn 4:5, X 16:9, Instagram 4:5/9:16), and runs a mandatory vision accuracy-check against the spec before anything is called done. `get_cost` preflight before every call; hard-stops at the PROFILE budget cap; free fallback is the spec plus a text wireframe. Higgsfield connector is optional. This skill should be used when the user says "make a data infographic", "turn these stats into an infographic", "make an infographic about [topic]", "visualize this survey", "visualize this report", "infographic of these numbers", or "render the data infographic".',
)
