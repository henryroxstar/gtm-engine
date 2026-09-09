"""Canonical manifest for the `content-studio` skill (scaffolded in Phase A).

Prompt body: plugin/skills/content-studio/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="content-studio",
    capability_tier=Tier.CORE,
    version="0.5.0",
    phase="2",
    description='Draft and lint a publish-ready, platform-native asset for the active company from a researched content item — across LinkedIn, X, and Instagram. Handles LinkedIn carousel/infographic/infographic-handwritten/text, X thread/single, and Instagram reel/carousel, and produces a genuinely localized variant when the item carries a non-primary locale. All platform variants derive from one shared research pack and brief (atomic repurposing, not N independent drafts). For X, selects a post structure (`pattern_id`) from the closed, de-branded catalog in docs/x-tweet-patterns.md — 3 candidates across distinct patterns, presented as an operator choice, same discipline as the hook-craft archetype workflow. Runs deterministic pre-generation checks before drafting, gates every asset through the content linter after drafting, then runs `python -m gtm_core.hook_score` to blend Part A retention, hook-bank prior, and pattern compliance, reworking or downgrading assets that score low/uncertain. Runs post-generation checks before setting status to review. Copies the item\'s hook_id, and for X the chosen pattern_id, into the asset JSON metadata when present for later attribution. Copy/brief-only — no PDF render and no paid image generation; visual render is a separate, operator-gated hand-off. This skill should be used when the user says "draft the post", "make the carousel", "build the asset", "content studio", "create the LinkedIn/X/Instagram post", "make the thread", "write the reel", or after content-research.',
)
