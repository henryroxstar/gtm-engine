# gtm_core/skills/builder_radar.py
"""Canonical manifest for the `builder-radar` skill (renamed from `journey-radar`;
internal state paths and the history `source:"journey"` tag are unchanged).

Prompt body: plugin/skills/builder-radar/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="builder-radar",
    capability_tier=Tier.CORE,
    version="0.3.0",
    phase="journey-m1",
    description="Scans the active profile's configured repo history — git commits and any project design docs — to surface story-worthy build moments as a StoryCluster[] (story-cluster.schema.json). Scores each moment 0–100 on narrative arc, concreteness, relatability, and soft product tie-in. Dedupes against content/<active>/history.jsonl (source:journey) so a shipped milestone is never re-told. Writes a dated digest and a clusters JSON under content/<active>/journey/radar/. Reads the journey watermark from content/<active>/journey/state.json for incremental (weekly) runs; the caller sets backfill mode by passing since_sha=first-commit. This skill should be used when the user says 'run builder radar', 'run journey radar', 'scan build history', 'what have we shipped that is worth a story', 'builder radar', or on the weekly builder-content cadence.",
)
