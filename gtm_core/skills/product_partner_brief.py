"""Canonical manifest for the `product-partner-brief` skill.

Prompt body: plugin/skills/product-partner-brief/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="product-partner-brief",
    capability_tier=Tier.CORE,
    version="0.2.0",
    phase="4",
    description=(
        "For a **product company** — a peer vendor that ships software, not a reseller and not a "
        "consultancy. Builds the partner-facing **collaboration brief** that a founder/CEO and a "
        "CTO/CPO read together when evaluating a technical product partnership: the joint "
        "opportunity, the seam between the two products, what each side brings, the competitive "
        "landscape, a standards anchor, a staged commercial shape, and the risks — including our "
        'own. Trigger when the user says "build a partnership brief for [company]", "where could we '
        'collaborate with [company]", "draft a collaboration proposal for [company]", "how do we '
        'partner with [company]", "integration proposal for [product]", or shares a peer vendor\'s '
        "repos/docs and asks where the two products fit together. The core method is the "
        "**self-named gap**: find the capability the partner's own type system, docs, non-goals, or "
        "roadmap already names but cannot produce, and show that our product is the shape that fits "
        "the slot — a gap they named is a roadmap conversation, a gap we assert is a pitch. Fails "
        "loudly when only an asserted gap exists. Enforces a **public-source gate** (every claim "
        "about either side resolves to a public URL — no internal paths, unreleased version "
        "numbers, or findings from private source shared under NDA), credit-before-gap ordering, an "
        "explicit layer boundary, two-sided value, no exclusivity by default, and disclosure of our "
        "own maturity risk. Delivers Markdown plus a polished HTML companion with purpose-built CSS "
        "infographics (positioning quadrant, capability ladder, framework coverage strip) — never "
        "raster infographics. Consumes an `account-dossier` when present; hands off to `build-deck`. "
        "Read-only and draft-only — never sends, posts, or commits to terms. Saves to "
        "`content/<active>/accounts/<account-slug>/`."
    ),
)
