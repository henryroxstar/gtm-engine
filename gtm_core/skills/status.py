"""Canonical manifest for the `status` skill.

SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="status",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="UX",
    description=(
        'Answer "where do I stand?" for the current prospecting list in plain words: whose move it is, what is held back and why, what is already in the sending tool. Trigger when the user says "where do I stand", "status", "what\'s waiting on me", or "how is my list doing".'
    ),
)
