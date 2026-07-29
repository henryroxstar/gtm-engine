"""Canonical manifest for the `solution-scope-check` skill.

Prompt body: plugin/skills/solution-scope-check/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
Product-agnostic (no requires_capability): works for any profile and for either
Mode-A (product-led) or Mode-B (bespoke) solution designs.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="solution-scope-check",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="5",
    description=(
        "The customer-facing **Scope Check** — a short, on-brand 2-page Word (.docx) worksheet the "
        "buyer marks up to **confirm or reshape the scope** — that runs at either of two moments: "
        "**pre-design**, sourced from a `solution-discovery` brief (page 1 = what we heard + the "
        "direction we're leaning; page 2 = the questions to answer so the design can be made right — "
        "validates scope *before* investing in the design), or **post-design**, sourced from a "
        "`solution-design` (page 1 = the solution simplified; page 2 = the residual assumptions the "
        "design rests on — confirm *before* build). The question bank is the same generic, "
        "decision-tagged set either way; only page 1's source differs. **Draft by default.** Trigger "
        'when the user says "scope check for [company]", "solution scope check", "scope validation '
        'for [company]", "validate the scope for [company]", "scope check before the design", "make '
        'a scope-check doc", "questions to validate the scope for [company]", or "simplify the '
        'solution design plus validation questions". Consumes a `solution-design` or '
        "`solution-discovery` dossier (and `account-dossier`) when present, and draws its questions "
        "from a generic question-type bank (`references/question-types.md`). Reads PROFILE for "
        "brand, byline, output folder, and language. Read-only; builds the .docx with python-docx "
        "(direct formatting, no style overrides). Produces files in "
        "content/<active>/accounts/<account-slug>/."
    ),
)
