"""Canonical manifest for the `seo-audit` skill.

Prompt body: plugin/skills/seo-audit/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="seo-audit",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="3D",
    description=(
        'Audit a target domain, investigate technical crawl issues, analyze ranking pages, evaluate search intent, and deliver an actionable data-backed SEO report. Trigger when the user says "audit SEO for [URL]", "SEO audit of [domain]", "crawl [site] for SEO issues", or "technical SEO review".'
    ),
)
