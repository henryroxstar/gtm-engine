"""Canonical manifest for the `infographic-data` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/infographic-data/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="infographic-data",
    capability_tier=Tier.PRODUCTION,
    version="0.3.0",
    phase="3B",
    description=(
        'Render postable data-dense editorial infographics with charts and callout stats using Higgsfield based on approved specs. Trigger when the user says "make a data infographic", "visualize this report", "turn these stats into an infographic", or "render the data infographic".'
    ),
)
