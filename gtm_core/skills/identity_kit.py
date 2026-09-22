# gtm_core/skills/identity_kit.py
"""Canonical manifest for the `identity-kit` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/identity-kit/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="identity-kit",
    capability_tier=Tier.CORE,
    version="0.4.0",
    phase="8",
    description=(
        'Manage brand visual identities, avatar handles, cloned voice IDs, and compliance disclosure configurations. Trigger when the user says "set up brand identity", "enroll reference elements", "register voice clone ID", "configure avatar handles", or "update disclosure line".'
    ),
)
