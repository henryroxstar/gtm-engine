"""Canonical manifest for the `email-sequence` skill.

Prompt body: plugin/skills/email-sequence/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.

Load-bearing invariant — the skill STAGES a
sequence (build) but never ACTIVATES it (send). Activation is human-only, by construction.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="email-sequence",
    capability_tier=Tier.CORE,
    version="0.17.0",
    phase="1",
    description="Turn composed outreach into a staged, multi-step email sequence in the connected "
    "sequencer — Saleshandy today, Apollo or GMass via a per-profile `email_tool` switch (a config "
    "change, not a rewrite). Composes the per-touch copy and cadence from the active profile's voice "
    "and the email craft guide (docs/email-optimization.md), writes a reviewable sequence spec to "
    "disk, then stages the whole sequence PAUSED in the tool (steps, A/B variants, schedule, enrolled "
    "leads) and STOPS. Activation is the operator's, never the skill's — it leaves the sequence "
    'paused and never resumes it. This skill should be used when the user says "build an email '
    'sequence", "set up a cold email cadence", "load these prospects into a sequence", "sequence '
    'this outreach", "put these prospects into Saleshandy", or "turn this outreach pack into a '
    'campaign". Reads sender identity, voice, language, and budget caps from the active profile. '
    "Runs an account-integrity gate (dossier coverage, domain integrity, competitor conflicts, and "
    "the row's research record — source URL, observed date, verbatim evidence, the fact's real "
    'subject, whether its "agents" are AI or people, and the send/re-angle/drop verdict) before '
    "staging copy and again before enrolling any batch, alongside the merge-render and compliance "
    "gates. Only send-verdict rows enroll. Gate output is budgeted: past ~15 unacknowledged warnings "
    "it reports each class as a rate and blocks, because a gate whose output nobody can read has the "
    "same effect as a gate that never ran. After the deterministic gates pass, a reading pass "
    "samples the list so the read covers it, records a verdict per email, ranks the send order, and "
    "separates defect classes that need a new rule from ones an existing rule should already have "
    "caught — a ranker, never a gate. Stages only — never activates or sends; the operator flips it "
    "live. Every spec declares the hook-matrix cell it implements (`hook_cell` + "
    "`argument_id`), so which argument a list receives is a checkable field rather than a "
    "judgement call; the campaign-level view is `gtm_core.hook_coverage`.",
    fallback_note="Without a connected sequencer (no `email_tool` set in PROFILE, or the provider's "
    "MCP is not connected), run the manual path: compose the full touch-by-touch plan — subjects, "
    "bodies, send-day offsets, and any A/B variants — grounded in the active profile's voice and "
    "docs/email-optimization.md, write it to the sequence spec on disk, and hand the operator a "
    "paste-ready plan to load into their tool by hand. This path needs no connector and is never a "
    "send path.",
)
