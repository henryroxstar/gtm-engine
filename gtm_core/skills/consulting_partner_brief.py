"""Canonical manifest for the `consulting-partner-brief` skill.

Prompt body: plugin/skills/consulting-partner-brief/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="consulting-partner-brief",
    capability_tier=Tier.CORE,
    version="0.2.0",
    phase="4",
    description=(
        "For a **consulting partner** — a systems integrator, dev shop, or consulting firm that both "
        "advises clients and builds custom solutions for them (not a reseller: they bill for design "
        "and delivery, they do not resell licences at margin). Turns that partner's published "
        "framework (whitepaper, methodology, maturity model, reference architecture) into a "
        "give-first, branded PDF that maps their framework onto the active company's products — "
        "honestly, layer by layer — and proves relevance in the industries the operator names. "
        'Trigger when the user says "map [partner]\'s framework to our products", "they published '
        'a whitepaper, build the partner mapping", "build a partner artifact for [company]", "where '
        'do we fit in [framework]", "mapping for this SI / dev shop / consultancy", or shares a '
        "partner whitepaper and names industries to focus on. Runs after `account-dossier` (who they are, "
        "how to engage) and feeds `draft-outreach`. Inventories every published artifact rather than "
        "the one handed over — a consulting partner usually has both a methodology and an "
        "architecture; asks the operator what regulation changed recently; assigns full/partial/none "
        "coverage per framework element, leads with the weakest, and shows the mapping as a review "
        "gate before building pages. Builds one page per use case (Pain / Why now / Claim / Gain) "
        "from the verified dossiers in `knowledge/use-cases/`, never from scratch, with at least one "
        "matched to the partner's home jurisdiction. Closes on an honest-ceiling page and emits an "
        "internal partner-mechanics note covering the commercial questions the PDF provokes. "
        "Abstract-only generated imagery (never text-in-image); renders HTML to PDF via headless "
        "Chrome and verifies every page visually. Read-only and draft-only — never sends or posts. "
        "Saves to `content/<active>/accounts/<account-slug>/`."
    ),
)
