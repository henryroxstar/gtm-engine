"""Canonical manifest for the `content-radar` skill (scaffolded in Phase A).

Prompt body: plugin/skills/content-radar/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="content-radar",
    capability_tier=Tier.PIPELINE,
    version="0.1.0",
    phase="1",
    description='News-driven content radar for the active company. Reads fresh PROD discovery_items via the read-only Postgres news MCP, dedupes against the content history, clusters stories by the active profile\'s content pillars, and scores each cluster 0–100 (blending the discovery trending_score with pillar-fit and relevance). DeepSeek (the worker MCP) writes bulk story summaries; Claude ranks and composes the brief. Produces a dated radar digest plus a StoryCluster[] the content-plan skill consumes. Falls back to a free web sweep (market-scan) when the news rows are stale or empty. This skill should be used when the user says "run content radar", "what\'s trending for content", "scan the news for content", "what should we post about", "content radar", "refresh the radar", or on the content cadence before planning.',
)
