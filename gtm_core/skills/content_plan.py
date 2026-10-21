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

**Direct-response steering (0.7.0, 2026-09-14).**
When planning an item with `goal: "conversion"`, Step 1 pairs it with one of the 5
frameworks in `docs/direct-response-patterns.md`, names the giveaway asset in `key_points`,
and leaves `brief.protagonist` unset so downstream execution bypasses the story graph.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="content-plan",
    capability_tier=Tier.CORE,
    version="0.7.0",
    phase="2",
    description=(
        'Propose the week\'s multi-platform content plan from radar digests, platform playbooks, and hook banks behind Gate 1 approval. Trigger when the user says "plan this week\'s content", "make a content plan", "what should we post this week", "draft the plan", or after a radar run.'
    ),
)
