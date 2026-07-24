"""Canonical manifest for the `campaign-plan` skill (Phase 4).

Prompt body: plugin/skills/campaign-plan/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen — never hand-edit it.

Standalone skill: it runs directly via the Skill tool / Telegram and is not wired into any pack.
If tenant gating is ever wanted it could later be added as a 4th independent root node in
`packs/planning/graphs/planning.toml` (document-only output → no external gate, `model_role =
"brain_plan"`) alongside gtm-plan / account-plan / event-plan; deferred for now.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="campaign-plan",
    capability_tier=Tier.CORE,
    version="0.1.1",
    phase="4",
    description=(
        "Build or refresh an executive-facing outbound program plan — a scaled, staged "
        "cross-org campaign grounded in the live prospect pipeline, current market signals, and "
        "the profile's ICP/scoring rubric. Use this skill when the user says \"build the campaign "
        'plan", "outbound program plan", "plan the outbound campaign", "refresh the campaign '
        'plan", "exec plan for the prospecting program", "present the outbound program to '
        'execs", or "how many SQLs from this program". Reads content/<active>/prospects/'
        "latest.json (pipeline density, tiers, heat, geo), the most recent market-signals snapshot, "
        "market-scan-config, and the profile's outbound-program-defaults + industry packs; produces "
        "a markdown plan (source of truth) plus a self-contained, theme-aware HTML exec companion. "
        "North-star metric is SQLs; every send stays human-gated — this skill plans, it never sends."
    ),
)
