"""Canonical manifest for the `case-study` skill.

Prompt body: plugin/skills/case-study/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen — never hand-edit it.
Product-agnostic (no requires_capability): works for any profile, and for a design-stage,
pilot, or fully-deployed engagement.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="case-study",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="4",
    description=(
        "Turn a solution design — or a delivered engagement — into a publishable 1-2 page "
        "customer story: Markdown, a self-contained HTML companion, a Word (.docx), and a "
        "quote-and-approval pack the customer's comms/legal team can sign off. "
        "**Evidence-tiered by construction:** *Deployed* produces a case study with measured "
        "outcomes, *Pilot* a pilot story scoped to the pilot's N and window, and *Design-stage* a "
        "**Solution Story** whose outcomes are explicitly modeled — never dressed up as achieved. "
        "Every number carries its basis (measured / customer-reported / modeled / unverified) and "
        "every named customer, person, and quote is tracked for sign-off; the skill never invents "
        "a metric, a quote, or a person. Customer-as-hero structure: results strip, why-now "
        "industry trigger, pain-claim-gain, what they tried first and why it failed, how it works, "
        'an applicability panel, and an evidence log. Trigger when the user says "write a case '
        'study for [company]", "turn this solution design into a case study", "make a case study", '
        '"customer story for [company]", "success story for [account]", "case-study one-pager", or '
        '"case study from the [company] design". Consumes `solution-design`, `solution-discovery`, '
        "and `account-dossier` outputs from the account folder when present, plus the profile's "
        "case-study proof library and industry pack. Reads PROFILE for brand, byline, output "
        "folder, and language. Read-only — never sends, publishes, or contacts anyone; produces "
        "files in content/<active>/accounts/<account-slug>/."
    ),
)
