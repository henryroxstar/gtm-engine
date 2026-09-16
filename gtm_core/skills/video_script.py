"""Canonical manifest for the `video-script` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/video-script/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-script",
    capability_tier=Tier.PIPELINE,
    version="0.22.0",
    phase="6",
    description=(
        'Turn an approved short-form video concept into a shot-by-shot script with cold opens, beats, captions, and visual prompts. Trigger when the user says "write the video script", "script this reel", "turn the plan into a short-form script", "write the hook and beats", or "script a 90-second video".'
    ),
)
