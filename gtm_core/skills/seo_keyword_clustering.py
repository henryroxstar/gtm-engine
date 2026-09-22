"""Canonical manifest for the `seo-keyword-clustering` skill.

Prompt body: plugin/skills/seo-keyword-clustering/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="seo-keyword-clustering",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="3D",
    description=(
        'Cluster keywords by search intent, detect cannibalization, and map them to existing or proposed URL architectures using OpenSEO MCP tools. Trigger when the user says "cluster these keywords", "group keywords by intent", "map keywords to pages", or "keyword clustering".'
    ),
)
