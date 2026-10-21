"""Canonical manifest for the `account-dossier` skill (scaffolded in Phase A).

Prompt body: plugin/skills/account-dossier/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="account-dossier",
    capability_tier=Tier.CORE,
    version="0.4.0",
    phase="4",
    description=(
        'Generate an on-brand executive or seller account dossier and meeting-prep brief as a Word docx or markdown research pack. Trigger when the user says "make a dossier for [account]", "prep me on [account/person]", "account dossier", "one-pager on [company]", or "prospecting brief for [account]".'
    ),
)
