"""Canonical manifest for the `content-publish` skill (scaffolded in Phase A).

Prompt body: plugin/skills/content-publish/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.

0.2.0 (Phase 12, §6.2): Step 1b checks a rendered item's identity_used against the
profile's disclosure line before staging, and the gate block gained the optional
⟦IDENTITY⟧ marker so the cockpit can re-verify the same check independently.

0.3.0 (video-quality-master §3.2 fix): Step 1b's identity_used enumeration gains
`generated` — an AI-generated/restyled asset with no likeness/voice handle still owes
the same Art. 50 disclosure. Fixes a fail-open where such an asset had no whitelisted
token to carry and identity_used silently read as empty.

0.5.0 (grade-a-plus §6): Step 0 reads the asset's finish.json to extract
hosted_media_urls for image/video assets; the ⟦MEDIA⟧ block is populated from hosted URLs
only. Local files are refused at the gate.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="content-publish",
    capability_tier=Tier.PIPELINE,
    version="0.5.0",
    phase="1",
    description='Stage a reviewed LinkedIn text post (plus hosted image/video media when present) for human-approved publishing to the active company\'s one pre-authorized LinkedIn account. This skill NEVER posts anything itself and NEVER calls any API, webhook, or curl — it only emits the exact post text inside a publish-gate block; the cockpit then shows that exact text in Telegram and publishes it ONLY after the operator presses "Approve & publish". The destination account is pinned server-side and is not selectable here. Before staging, reads the asset\'s finish.json and uses hosted_media_urls only; local media files are refused and the operator is told to run media-host or post manually. Before staging, confirms deterministic post-generation quality checks (`python -m gtm_core.content_quality post`) passed — failing closed on linter errors, missing disclosure, or objective video-finish failures. For a rendered video/clip/restyle item, checks its identity_used against the profile\'s BRAND.toml disclosure line before staging (EU AI Act Article 50) and carries the identity list into the gate block for the cockpit to re-verify independently. Use when the user says "publish it", "post this to LinkedIn", "ship the post", "send it", or after content-studio/video-finish has produced a linted asset the user wants live.',
)
