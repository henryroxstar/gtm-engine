# gtm_core/skills/demo_capture.py
"""Canonical manifest for the `demo-capture` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/demo-capture/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="demo-capture",
    capability_tier=Tier.PRODUCTION,
    version="0.2.0",
    phase="C",
    description=(
        'Transform local product screen recordings or phone footage into polished short-form demo clips via Reap. Trigger when the user says "clip this recording", "turn footage into shorts", "make a demo clip", "I recorded myself", or "repurpose local video".'
    ),
)
