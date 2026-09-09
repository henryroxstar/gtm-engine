"""Canonical manifest for the `content-plan` skill (scaffolded in Phase A).

Prompt body: plugin/skills/content-plan/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.

**The story spine (0.6.0, 2026-09-07).** Step 1 derives three optional `brief` fields in order —
`core_value` (the human value the pillar stands for, one word), `opposite` (that value's opposite,
read from the persona's *believed-but-never-said* line in `audience-psychology.md` rather than
invented), and `protagonist` (who the journey from opposite to value costs most). The persona block
is the protagonist template, and `brief.protagonist` is what marks an item as story-format for
every stage downstream. Step 1 also carries the routing rule that decides the shape: information
pushes out emotion, so a reach-goal item aimed at a cold persona is not an explainer. The third of
the nine-item change list in the 2026-09-07 storytelling-method PRD.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="content-plan",
    capability_tier=Tier.CORE,
    version="0.6.0",
    phase="2",
    description='Propose the week\'s content plan for the active company from the latest radar digests. Loads the last few content-radar digests, the platform playbooks, the hook BANK (knowledge/hooks.toml — not hook-matrix.md, which holds 1:1 outreach openers), and the content history, then proposes a weekly theme plus concrete content ideas — the count follows the profile\'s declared cadence (content-priority.md) when set, otherwise 3–5 — each tied to a pillar, a story cluster, a journey stage, a goal, and an optional hook_id for attribution (paired so the hook actually declares the item\'s format, which is what lets hook_score run downstream), with platform, format, and locale, across LinkedIn, X, and Instagram — one item per platform, plus optional localized variants (a separate item per non-primary locale) for the two-clock rule. Presents the plan in Telegram behind Gate 1 (Approve / Edit / Reject) — nothing is finalized without the user\'s approval. On approval it writes the week\'s plan as a ContentItem[]. Runs a deterministic pre-generation quality gate and surfaces warnings/blockers in the Gate 1 message. This skill should be used when the user says "plan this week\'s content", "make a content plan", "what should we post this week", "content plan", "draft the plan", or after a radar run.',
)
