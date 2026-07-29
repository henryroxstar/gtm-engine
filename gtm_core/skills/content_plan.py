"""Canonical manifest for the `content-plan` skill (scaffolded in Phase A).

Prompt body: plugin/skills/content-plan/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="content-plan",
    capability_tier=Tier.CORE,
    version="0.2.0",
    phase="2",
    description='Propose the week\'s content plan for the active company from the latest radar digests. Loads the last few content-radar digests, the platform playbooks, and the content history, then proposes a weekly theme plus 3–5 concrete content ideas (each tied to a pillar and a story cluster, with platform, format, and locale) across LinkedIn, X, and Instagram — one item per platform, plus optional localized variants (a separate item per non-primary locale) for the two-clock rule. Presents the plan in Telegram behind Gate 1 (Approve / Edit / Reject) — nothing is finalized without the user\'s approval. On approval it writes the week\'s plan as a ContentItem[]. This skill should be used when the user says "plan this week\'s content", "make a content plan", "what should we post this week", "content plan", "draft the plan", or after a radar run.',
)
