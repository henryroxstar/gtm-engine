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
    description='Generate a short, on-brand account + buyer dossier as a Word (.docx) that lets a non-technical seller walk into a meeting prepared, assuming zero prior context on the account or buyer. A friendly ~4-page prep briefing — NOT a technical solution design or a deck. Also supports a hard 1-page exec one-pager variant (references/exec-onepager-template.json) for a principal who already knows how to run the call and needs only a product brief + credibility read, not seller coaching, a hard 1-page prospecting-brief variant (references/prospecting-brief-template.json) for a cold, scored-but-unmet Tier-A lead with no meeting booked yet — understand-the-business + likely-needs + concrete product-fit, no deep research pass, AND a markdown-only research-pack variant for bulk, mixed-tier dossier coverage where even the prospecting-brief docx render is too much per-account overhead. Trigger when the user says "make a dossier for [account]", "prep me on [account/person]", "account dossier", "brief for my meeting with [name]", "one-pager on [company] and [buyer]", "exec one-pager for [principal] on [account]", "prospecting brief for [account]", "cold brief on [account]", "research pack for [account]", "who is [buyer] at [account] and how do I engage", or any similar request for a standalone, forward-to-a-seller (or forward-to-an-exec) meeting-prep doc. Research-first: mines any provided materials and prior skill outputs, verifies time-sensitive facts (funding, leadership, launches, regulatory dates) against fresh web sources (skipped for the prospecting-brief and research-pack variants), then builds the .docx via the docx skill (or a plain markdown file for the research-pack variant). Reads PROFILE for brand, byline, output folder, and language.',
)
