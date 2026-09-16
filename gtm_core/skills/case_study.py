"""Canonical manifest for the `case-study` skill.

Prompt body: plugin/skills/case-study/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen — never hand-edit it.
Product-agnostic (no requires_capability): works for any profile, and for a design-stage,
pilot, or fully-deployed engagement.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="case-study",
    capability_tier=Tier.CORE,
    version="0.4.0",
    phase="4",
    description=(
        'Extract authentic customer success stories and proof points into structured case studies following the interview-protocol. Trigger when the user says "write a case study", "customer story for [account]", "turn this win into a case study", or "document customer outcome".'
    ),
)
