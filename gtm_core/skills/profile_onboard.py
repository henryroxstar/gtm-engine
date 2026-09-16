# gtm_core/skills/profile_onboard.py
"""Canonical manifest for the `profile-onboard` skill.

Prompt body: plugin/skills/profile-onboard/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.

Not product-bound (no requires_capability): this is the onboarding entry that
*creates* a profile bundle from source text, so it cannot depend on a profile's
products existing yet. agent/onboard.py loads the prompt from body_template.md.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="profile-onboard",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="onboard",
    description=(
        'Extract structured profile drafts from company websites, PDFs, or source text to stage new tenant bundles for review. Trigger when onboarding a new company or when the user says "onboard [company]", "set up a profile from this site/PDF", "extract a profile draft", or runs /onboard.'
    ),
)
