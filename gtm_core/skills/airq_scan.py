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
    description='Run an AIRQ-aligned agent-security assessment of a target company\'s AI agent product — from a GitHub repo, a website, pasted text, or a screenshot — and turn it into two LinkedIn-ready infographics plus give-first outreach copy. Scores all 21 AIRQ factors (Attack Surface, Blast Radius, Defense Controls) with honest evidence tiers, detects the Lethal Trifecta, and places the agent in a quadrant — every number pinned in a spec at a plan gate before any paid call. Produces a reusable AIRQ explainer image and a target-specific audit report card whose gaps are tagged to the active company\'s mitigating products, then drafts a cold DM and a public comment that lead with the assessment as a gift and never pitch. Runs a mandatory vision accuracy-check against the spec; `get_cost` preflight before every paid call; hard-stops at the PROFILE budget cap; free fallback is the spec plus a text wireframe. This skill should be used when the user says "run an AIRQ scan on [URL]", "assess [company] AI agent risk", "AIRQ audit [URL/screenshot]", "score [company]\'s agent security", or "make the AIRQ infographic for [company]". Drafts only — never posts; the assessment is indicative (public signals), not an official AIRQ audit.',
)
