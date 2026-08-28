"""Canonical manifest for the `infographic-handwritten` skill.

Prompt body: plugin/skills/infographic-handwritten/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="infographic-handwritten",
    capability_tier=Tier.PRODUCTION,
    version="0.3.0",
    phase="3B",
    description='Render a finished, postable handwritten-style infographic — a single image that looks like a real notebook page, whiteboard, or formula sheet, hand-lettered with ballpoint or marker, on paper or grid texture — from a brief, framework, formula set, or mental model, using Higgsfield. Pins every element and label in an approved spec at a plan gate before any paid call, re-flows the layout per platform (LinkedIn 4:5, X 16:9, Instagram 4:5/9:16), and runs a mandatory vision accuracy-check (text correct + legible; stylistic imperfection allowed) against the spec before anything is called done. `get_cost` preflight before every call; hard-stops at the PROFILE budget cap; free fallback is the spec plus a text wireframe. Higgsfield connector is optional. This skill should be used when the user says "make a handwritten infographic", "whiteboard-style graphic", "notebook sketch of [framework]", "formula sheet for [topic]", "sketch this framework", "hand-lettered graphic", "make it look handwritten", "notebook page about [topic]", or "hand-drawn visual".',
)
