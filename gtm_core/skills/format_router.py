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
        "Cross-modal format dispatcher. Given an approved hook and pillar, decides which "
        "(format, platform) tuples to produce by respecting the profile's content-priority.md mix, "
        "the hook's declared formats, cost budget, hook fatigue, and the composite hook_score. "
        "Emits one ContentItem per selected tuple; when video is selected it hands off to the "
        "existing video-router skill to pick the specific video lane. Does not spend credits. "
        "This skill should be used when the user says 'route this hook', 'which formats should "
        "we produce', 'cross-modal plan', or as the plan-stage dispatcher in the creator "
        "cross-modal campaign pack."
    ),
)
