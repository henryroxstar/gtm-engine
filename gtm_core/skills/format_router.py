"""Canonical manifest for the `format-router` skill.

Prompt body: plugin/skills/format-router/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.

Cross-modal format dispatcher. Given an approved hook and pillar, it decides which
(format, platform) tuples to produce, respecting the profile's content mix, the hook's
declared formats, cost budget, hook fatigue, and the composite hook_score. When video is
selected it hands off to the existing `video-router` skill to pick the specific video lane.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="format-router",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="3",
    description=(
        'Route approved hooks and pillars across formats and platforms based on profile mix, budgets, and historical hook scores. Trigger when the user says "route this hook", "which formats should we produce", "cross-modal plan", or during campaign planning.'
    ),
)
