"""Canonical manifest for the `market-intelligence` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/market-intelligence/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="market-intelligence",
    capability_tier=Tier.CORE,
    version="0.10.1",
    phase="4",
    description=(
        'Turn field data into an educational intelligence brief for product and engineering teams, synthesizing customer, competitor, and standards signals. Trigger when the user says "run the market-intelligence brief", "market intelligence", "voice of the customer", "what should we build next", or "customer pain report for product".'
    ),
)
