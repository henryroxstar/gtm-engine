"""Canonical manifest for the `metrics-review` skill.

Prompt body: plugin/skills/metrics-review/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="metrics-review",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="3D",
    description=(
        'Analyze product analytics telemetry (WAU, feature adoption, onboarding funnels) to generate an executive product health report and identify growth bottlenecks. Trigger when the user says "review product metrics", "analyze WAU", "feature adoption review", or "product health report".'
    ),
)
