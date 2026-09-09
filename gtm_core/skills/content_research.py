"""Canonical manifest for the `content-research` skill (scaffolded in Phase A).

Prompt body: plugin/skills/content-research/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="content-research",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="1",
    description='Research a planned content item into verifiable, citable material for the active company. For a given ContentItem from the week\'s plan, gathers 3–6 verifiable facts (each with a source), strong quotables, credible counterpoints, and an explicit "claims to avoid / needs a caveat" list. DeepSeek (the worker MCP) does bulk extraction; Claude verifies every claim against the source and drops anything unsupported. Free web by default via the Firecrawl MCP. This skill should be used when the user says "research this item", "research the content", "get facts for the post", "content research", or after a content plan is approved.',
)
