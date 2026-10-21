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
        'Capture stage of the demo-clips video lane (routed via video-router): uploads an approved local product screen recording or phone capture to Reap and creates polished short-form demo clips. Trigger when the user says "run demo-capture on [file]", "clip this approved screen recording", or as the capture stage of the video pack.'
    ),
)
