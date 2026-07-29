"""Canonical manifest for the `content-publish` skill (scaffolded in Phase A).

Prompt body: plugin/skills/content-publish/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="content-publish",
    capability_tier=Tier.PIPELINE,
    version="0.1.0",
    phase="1",
    description='Stage a reviewed LinkedIn text post for human-approved publishing to the active company\'s one pre-authorized LinkedIn account. This skill NEVER posts anything itself and NEVER calls any API, webhook, or curl — it only emits the exact post text inside a publish-gate block; the cockpit then shows that exact text in Telegram and publishes it ONLY after the operator presses "Approve & publish". The destination account is pinned server-side and is not selectable here. Use when the user says "publish it", "post this to LinkedIn", "ship the post", "send it", or after content-studio has produced a linted text asset the user wants live. LinkedIn text posts only in Phase 1 (carousels are posted manually until PDF hosting exists).',
)
