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
    description="Close the loop between reading emails and improving them. Runs the four modes of "
    "the email-quality program: `judge` scores every row of a staged sequence against the rubric "
    "with a cheap pinned model and writes a verdict per row; `sheet` builds the blind labeling "
    "sheet an operator fills in and seals a class-balanced holdout from it; `apply` turns those "
    "human labels into durable changes to the next send list — disqualifying bad-fit accounts and "
    "suppressing wrong-person addresses through both the lifecycle status and the suppression "
    "ledger; `report` scores the judge against the sealed holdout beside the deterministic rule "
    "fleet, and gives every linter rule a keep / recalibrate / delete verdict from its fire rate "
    'crossed with human evidence. This skill should be used when the user says "improve email '
    'quality", "run an email eval", "score these emails", "judge this sequence", "review the '
    'outreach copy", "why are our emails not getting replies", "which linter rules are pulling '
    'their weight", or "apply the eval labels". The judge RANKS and never blocks — it writes a '
    "verdict column, and the deterministic account-integrity gate is what refuses a row at "
    "enrollment. An optional repair pass re-composes rows the judge rejects, capped at three "
    "attempts by the CLI rather than by the model, and repaired rows are excluded from the "
    "validation holdout because labelling copy the judge shaped and then validating the judge on "
    "it is circular. Touches no send path: sequences stay paused and activation stays the "
    "operator's.",
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
