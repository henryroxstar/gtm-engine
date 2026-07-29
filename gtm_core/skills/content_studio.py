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
    version="0.2.1",
    phase="2",
    description='Draft and lint a publish-ready, platform-native asset for the active company from a researched content item — across LinkedIn, X, and Instagram. Handles LinkedIn carousel/infographic/infographic-handwritten/text, X thread/single, and Instagram reel/carousel, and produces a genuinely localized variant when the item carries a non-primary locale. All platform variants derive from one shared research pack and brief (atomic repurposing, not N independent drafts). Gates EVERY asset through the content linter before it is shown for review. Copy/brief-only — no PDF render and no paid image generation; visual render is a separate, operator-gated hand-off. This skill should be used when the user says "draft the post", "make the carousel", "build the asset", "content studio", "create the LinkedIn/X/Instagram post", "make the thread", "write the reel", or after content-research.',
)
