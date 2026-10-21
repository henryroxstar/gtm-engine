"""Canonical manifest for the `seo-keyword-research` skill.

Prompt body: plugin/skills/seo-keyword-research/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="seo-keyword-research",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="3D",
    description=(
        'Discover high-intent keyword opportunities, evaluate difficulty and volume metrics, inspect live SERPs, and save priority terms using OpenSEO MCP tools. Trigger when the user says "find keywords for [topic]", "keyword research for [domain]", "high volume keywords for [seed]", or "SEO keyword research".'
    ),
)
