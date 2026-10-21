"""Canonical manifest for the `email-quality` skill.

Prompt body: plugin/skills/email-quality/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.

Load-bearing invariant — the judge
this skill runs is a RANKER, never a gate: it writes a verdict data column and the
deterministic `account_integrity --require-verdict send` is what refuses a row. Nothing
here touches the send path; sequences stay PAUSED and activation stays human-only.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="email-quality",
    capability_tier=Tier.PIPELINE,
    version="0.3.0",
    phase="1",
    description=(
        'Evaluate and score outbound email drafts against voice, spam triggers, length constraints, and relevance rubrics. Trigger when the user says "audit email quality", "score outbound drafts", "check cold email copy", "review outreach before sending", or as QA gate in prospecting.'
    ),
    fallback_note="The judge picks its transport automatically and needs no configuration: with "
    "ANTHROPIC_API_KEY set it scores one row per request against the Anthropic API; without one it "
    "falls back to the Agent SDK on the host's own auth (an OAuth subscription in a local Claude "
    "Code session), batching a few rows per prompt to amortise subprocess cost. Both record "
    "`backend` and `judge_batch` on every row, so a holdout scored across the two is a visible "
    "confound rather than a silent one. If BOTH are unavailable — no key and an expired or revoked "
    "OAuth token — the judge returns an error payload and the reading pass reverts to what it was "
    "before this skill existed: sample the list with `gtm_core.adjudication sample`, read the "
    "rendered emails yourself, and record one adjudication JSONL line per email by hand. Every "
    "other mode — `sheet`, `apply`, `report` — is pure deterministic Python and works unchanged, "
    "because the human labels, not the judge, are the ground truth the whole program is built on.",
)
