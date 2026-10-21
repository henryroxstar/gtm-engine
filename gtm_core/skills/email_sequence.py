"""Canonical manifest for the `email-sequence` skill.

Prompt body: plugin/skills/email-sequence/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.

Load-bearing invariant — the skill STAGES a
sequence (build) but never ACTIVATES it (send). Activation is human-only, by construction.

As of 0.19.0, the skill also never ENROLLS a lead: `add_leads_to_sequence` /
`import_prospects_to_sequence` are denied to it outright (agent/permissions.py). It composes a
reviewable enrollment plan and writes it to a `.pending/*.enroll-draft.json` file for the pack
graph's `sequence` gate to hold; the actual enrollment call — the PII egress — is dispatched by
Python only, after operator approval (`agent/email_dispatch.py`, driven from the pack graph's
`sequence-enroll` node). See the email-sequence design PRD and its 2026-09-14 addendum for the
full rationale.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="email-sequence",
    capability_tier=Tier.CORE,
    version="0.20.0",
    phase="1",
    description=(
        'Build structured multi-step email sequences staged in sequencer platforms in a paused state for human activation. Trigger when the user says "build email sequence for [persona]", "create cold outreach sequence", "stage sequence in sequencer", or "draft drip campaign".'
    ),
    fallback_note="Without a connected sequencer (no `email_tool` set in PROFILE, or the provider's "
    "MCP is not connected), run the manual path: compose the full touch-by-touch plan — subjects, "
    "bodies, send-day offsets, and any A/B variants — grounded in the active profile's voice and "
    "docs/email-optimization.md, write it to the sequence spec on disk, and hand the operator a "
    "paste-ready plan to load into their tool by hand. This path needs no connector and is never a "
    "send path.",
)
