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
    description="Turn the people who engaged with a LinkedIn post — reactors "
    "(like/celebrate/support/love/insight/funny) and commenters — into a qualified prospect list. "
    "Default is manual-assisted: the operator opens the post's reactions/comments list and pastes the "
    "text or sends a screenshot, and the skill parses it; driving the operator's already-logged-in "
    "browser is an explicit opt-in fast path and only works in a local session (never headless). "
    "Extracts each person's name, headline, engagement type, and any comment, qualifies them against "
    "the active profile's ICP personas, and upserts them into a persistent, tag-filterable people "
    "ledger (content/<active>/prospects/people.json via the gtm_core.people CLI) plus a HubSpot-ready "
    "CSV for import — tracking engagement history and conversion (lead → opportunity → account) "
    "without overwriting the prospect skill's company-grained list. This "
    'skill should be used when the user says "add the people who liked this post", "who engaged with '
    'this post", "build a prospect list from this post\'s reactions / comments", or "capture the '
    'engagers". Manual capture is free; optional contact enrichment respects the budget cap. Drafts a '
    "list only — never messages anyone.",
    fallback_note="Without browser automation or paid enrichment, run the manual-assisted path (the "
    "default): ask the colleague to open the post, click the reaction count to expand the reactions "
    "list, and expand comments, then paste the visible text or send a screenshot. Parse name + "
    "headline + engagement type (+ any comment) from what they share, qualify each against the ICP "
    "personas, and write the person-grained CSV + JSON sidecar with provenance. Leave Email blank when "
    "it is not verified rather than guess. This path needs no connector and no logged-in browser "
    "beyond the colleague's own screen.",
)
