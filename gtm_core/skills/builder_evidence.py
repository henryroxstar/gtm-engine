# gtm_core/skills/builder_evidence.py
"""Canonical manifest for the `builder-evidence` skill (renamed from
`journey-evidence`; internal state paths and the history `source:"journey"` tag
are unchanged).

Prompt body: plugin/skills/builder-evidence/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="builder-evidence",
    capability_tier=Tier.CORE,
    version="0.4.0",
    phase="journey-m2",
    description="Assembles the evidence pack for one builder-story build moment. Reads the chosen StoryCluster from content/<active>/journey/radar/, fetches the actual git commits, diffs, and design-doc text for its source_items via gtm_core.journey.gitscan, and compiles a structured evidence pack to content/<active>/journey/evidence/<id>.md. Evidence is primary-source only — no external fetches, no fact-checking against web sources. Equivalent to content-research in the news pipeline but for build history. This skill should be used after builder-radar when the user says 'gather evidence for this moment', 'pull the commits for this story', 'research this build moment', or after Gate 1 plan approval for a builder/journey item.",
)
