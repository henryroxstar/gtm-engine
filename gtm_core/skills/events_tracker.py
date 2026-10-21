"""Canonical manifest for the `events-tracker` skill (scaffolded in Phase A).

Prompt body: plugin/skills/events-tracker/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="events-tracker",
    capability_tier=Tier.CORE,
    version="1.0.0",
    phase="2",
    description=(
        'Scan upcoming industry events and meetups, compute travel budgets, track conference pipelines, and extract attendees into prospect stores. Trigger when the user says "run my events scan", "track events", "what events are coming up", "update my events spreadsheet", "what conferences should I attend", or "pull the guest list".'
    ),
)
