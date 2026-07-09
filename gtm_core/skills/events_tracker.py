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
    description='Weekly GTM events scan and travel-budget tracker. Scans Luma, Eventbrite, Meetup, and the open web for conferences and meetups in the active profile\'s product category, near the profile\'s home base and target cities; filters by topic and geography; computes per-event travel cost against the profile\'s travel policy; and updates a single events spreadsheet in place — preserving all Status and Priority edits. Uses Firecrawl (if connected, budget-guarded) with a browser/web-search fallback. Product-agnostic: the event topic comes from the active profile\'s product category, never hardcoded. Use when the user says "run my events scan", "track events", "what events are coming up", "update my events spreadsheet", "find [category] meetups near me", "what conferences should I attend", or on the weekly cadence. Also handles on-demand prospect extraction from a single event: when the user says "extract prospects/attendees/speakers from this event", "pull the guest list", "who\'s going to [event]", or shares an event URL/screenshot, it scrapes the public attendee/speaker list via the Firecrawl MCP tool (or WebFetch / vision OCR for a screenshot) — never a shell command — and writes the people to the prospect store.',
)
