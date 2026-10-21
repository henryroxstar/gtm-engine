"""Capability tiers for gtm_core skills.

A skill declares ONE tier; the runtime decides what to do with it. Free runtimes
(the Cowork plugin) run CORE directly and fall back for higher tiers; paid
runtimes (backend API, MCP server) gate on the caller's entitlement.

This module is intentionally just the enum. Phase B adds the resolver in
`gtm_core.capabilities` (effective tier = intersection of what's technically
available with what the caller is entitled to). Enforcement lives at each
runtime boundary — never inside a skill.

Tiers:
  CORE        — always available; free everywhere (user brings their own key).
  PIPELINE    — needs server-side connectors/credentials (news DB, worker, publish).
  PRODUCTION  — heavy compute (TTS, video, render); pro plan only.
"""

from __future__ import annotations

from enum import Enum


class Tier(str, Enum):
    """A skill's capability tier. `str` mixin → the value serialises cleanly
    into SKILL.md frontmatter and JSON (`Tier.CORE.value == "core"`)."""

    CORE = "core"
    PIPELINE = "pipeline"
    PRODUCTION = "production"
