# gtm_core/skills/identity_kit.py
"""Canonical manifest for the `identity-kit` skill.

Prompt body: plugin/skills/identity-kit/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.

Onboarding-family, like `profile-onboard` (Tier.CORE, phase="onboard"): the family
allowed to write under profiles/<active>/. Pipeline skills (video-render, video-score,
...) stay read-only there. Unlike profile-onboard (which emits a ProfileDraft that
Python then writes), identity-kit writes directly — but ONLY through
`python -m gtm_core.brandkit --set identity.<key>` (gtm_core/brandkit.py), which
keeps _safe_segment in the loop and verifies every write before it lands. The skill
itself never Edits/Writes the BRAND.toml file.

phase="8" rather than "onboard":
this runs any time after initial onboarding, on request, not once at profile creation.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="identity-kit",
    capability_tier=Tier.CORE,
    version="0.4.0",
    phase="8",
    description=(
        'Manage brand visual identities, avatar handles, cloned voice IDs, and compliance disclosure configurations. Trigger when the user says "set up brand identity", "enroll reference elements", "register voice clone ID", "configure avatar handles", or "update disclosure line".'
    ),
)
