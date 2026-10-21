"""Canonical manifest for the `linkedin-engagers` skill (Phase 3C).

Prompt body: plugin/skills/linkedin-engagers/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="linkedin-engagers",
    capability_tier=Tier.CORE,
    version="0.2.0",
    phase="3C",
    description=(
        'Convert LinkedIn post reactions and comments into qualified prospects in the people ledger and exportable CSVs. Trigger when the user says "add the people who liked this post", "who engaged with this post", "build a prospect list from this post\'s reactions / comments", or "capture the engagers".'
    ),
    fallback_note="Without browser automation or paid enrichment, run the manual-assisted path (the "
    "default): ask the colleague to open the post, click the reaction count to expand the reactions "
    "list, and expand comments, then paste the visible text or send a screenshot. Parse name + "
    "headline + engagement type (+ any comment) from what they share, qualify each against the ICP "
    "personas, and write the person-grained CSV + JSON sidecar with provenance. Leave Email blank when "
    "it is not verified rather than guess. This path needs no connector and no logged-in browser "
    "beyond the colleague's own screen.",
)
