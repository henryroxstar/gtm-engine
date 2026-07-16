"""Canonical manifest for the `linkedin-reply` skill (Phase 3C).

Prompt body: plugin/skills/linkedin-reply/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="linkedin-reply",
    capability_tier=Tier.CORE,
    version="0.3.0",
    phase="3C",
    description="Craft a soft-sell reply to a LinkedIn post — a value-first public comment (and an "
    "optional DM / connection note) that genuinely engages the poster's specific point, adds one "
    "substantive contribution, then optionally bridges to what the active company builds in the same "
    "space, with a link only if it truly helps the reader. Reads the post from pasted text, a "
    "screenshot, or a URL (degrades gracefully when the URL is blocked); loads voice, hooks, and case "
    "studies from the active profile and runs the voice self-check. This skill should be used when the "
    'user says "reply to this LinkedIn post", "comment on this post", "draft a soft-sell reply", '
    '"respond to this post / screenshot", or shares a LinkedIn post URL or screenshot and wants a '
    "reply. Drafts only — never posts; a link defaults to a first comment, never the comment body. For "
    "a cold first-touch with no prior post, use draft-outreach instead.",
)
