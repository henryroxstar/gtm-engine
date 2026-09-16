"""Canonical manifest for the `inbound-triage` skill.

Prompt body: plugin/skills/inbound-triage/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.

Load-bearing invariant — the
skill READS inbound replies and DRAFTS a gated reply (⟦GATE:reply⟧); it never sends.
Confidence decides urgency and whether to draft, never whether a human approves.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="inbound-triage",
    capability_tier=Tier.CORE,
    version="0.2.0",
    phase="1",
    description=(
        'Classify and triage inbound replies by intent and priority, drafting response artifacts for human review behind Gate 3. Trigger when the user says "triage my replies", "check inbound", "who replied", "draft replies to these responses", or "what came back from outreach".'
    ),
    fallback_note="Without a connected inbox (`inbound_source: manual`, or the provider MCP not "
    "connected), the skill cannot pull replies automatically — ask the operator to paste the reply "
    "text (or forward the thread), then classify and draft against the same rubric. The output is "
    "identical: a gated reply draft the operator approves and sends by hand. This path is never a "
    "send path.",
)
