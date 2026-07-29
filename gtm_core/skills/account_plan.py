"""Canonical manifest for the `account-plan` skill (scaffolded in Phase A).

Prompt body: plugin/skills/account-plan/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="account-plan",
    capability_tier=Tier.CORE,
    version="0.4.0",
    phase="3",
    description='Build a CRO-grade strategic account plan for one target company — deal stage + forecast category, ICP score, a MEDDPICC scorecard, a Miller Heiman buying-influence map (response modes, ratings, win-results), champion development plan, value hypothesis + commercial model (flags a missing pricing model as a blocker), decision/paper process, competitive strategy, a risk register, and a mutual action plan sequenced to a real commercial event with owners and dates. This skill should be used when the user says "build an account plan for [company]", "strategic plan for [account]", "account plan for [company]", "plan my approach to [company]", "how do I land [company]", or "help me develop [account]". Reads PROFILE for markets and ICP weighting. Saves the plan (+ a rendered .docx) to the account folder (`content/<active>/accounts/<account-slug>/`). For a lighter pre-call brief, use call-prep instead.',
)
