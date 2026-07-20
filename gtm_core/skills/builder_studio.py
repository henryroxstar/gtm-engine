# gtm_core/skills/builder_studio.py
"""Canonical manifest for the `builder-studio` skill (renamed from
`journey-studio`; internal state paths and the history `source:"journey"` tag
are unchanged).

Prompt body: plugin/skills/builder-studio/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="builder-studio",
    capability_tier=Tier.CORE,
    version="0.5.0",
    phase="journey-m2",
    description="Drafts the asset bundle for one builder-story build moment in the founder's voice: a LinkedIn text post, a longer article in markdown, a solo-founder monologue podcast script, and — when the moment has an architecture or flow at its core — an optional diagram brief. Reads profiles/<active>/knowledge/voice.md and PROFILE.md for voice, plus case-studies.md, icp-personas.md, and audience-psychology.md for proof points and audience aim; builds hooks from docs/hook-craft.md (three candidates) and reads the evidence pack for facts; weaves a mandatory-outcome free-web trend tie-in (trending + insider deep-cut registers, recorded skip otherwise) and an adversarial claim check before surfacing. Runs the content linter (content_linter.py, including the advisory prose-quality pass) on the LinkedIn asset and the safe-to-share lint (lint_safe_to_share) over all outputs before surfacing for review. The LinkedIn asset.json feeds directly to content-publish (Gate 2); article.md and podcast-script.md are manual-publish artifacts in v1, and the article can be repurposed into a carousel via carousel-pdf. This skill should be used after builder-evidence when the user says 'write the builder post', 'write the journey post', 'draft the founder story', 'create the build article', 'write the podcast script', 'builder studio', 'journey studio', or after Gate 1 plan approval for a builder/journey item.",
)
