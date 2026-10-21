"""Canonical manifest for the `demo-narrative` skill.

Prompt body: plugin/skills/demo-narrative/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
Product-agnostic (no requires_capability): every fact it states comes from the active
profile, so it works for any tenant and for either a product-led or a bespoke engagement.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="demo-narrative",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="5",
    description=(
        'Design the live sales-call demo flow for a target account — the outcome first, the moments that earn the meeting, and what is real versus staged (for rendering a video clip, use video-router). Trigger when the user says "plan the demo for [company]", "live demo script", "demo narrative", or "what should I show [account]".'
    ),
)
