"""Canonical manifest for the `seo-competitor-analysis` skill.

Prompt body: plugin/skills/seo-competitor-analysis/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="seo-competitor-analysis",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="3D",
    description=(
        'Analyze one competitor\'s organic footprint, ranking keywords, content themes, backlinks, and search gaps using OpenSEO MCP tools. Trigger when the user says "analyze competitor SEO for [domain]", "competitor keyword gap for [domain]", "SEO competitor analysis", or "how is [competitor] ranking".'
    ),
)
