"""Canonical manifest for the `solution-discovery` skill (scaffolded in Phase A).

Prompt body: plugin/skills/solution-discovery/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="solution-discovery",
    capability_tier=Tier.CORE,
    version="0.1.2",
    phase="5",
    requires_capability=("technical-discovery",),
    description='Prepare for a technical deep-dive by profiling the account\'s engineering stack and gathering functional scope (jobs-to-be-done, features, V1/V2 intuition), then producing a requirements question bank where every question is mapped to the design decision it unlocks. In Mode B (no product to map onto), profiles the prospect\'s manual workflow and tags the agent-shaped loop instead. Trigger when the user says "prep me for the technical deep-dive with [company]", "solution discovery for [company]", "what technical questions should I ask [company]", "profile [company]\'s stack for the architecture call", "surface requirements for [company]", "SA prep for [company]", or "technical discovery for [company]". Business-level call prep is the `call-prep` skill — this one goes deeper technical and feeds `solution-design`. Reads PROFILE for markets/budget. Read-only research; never sends anything. Produces a discovery brief saved to the account folder (`content/<active>/accounts/<account-slug>/`).',
)
