# gtm_core/skills/video_restyle.py
"""Canonical manifest for the `video-restyle` skill.

The restyle lane (Phase 11).
Prompt body: plugin/skills/video-restyle/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.

Pack-layer only: a new skill on the unmodified engine, referenced by the new
`restyle-shorts.toml` pack variant. No engine change.

Interactive-first by construction, not by choice: `media_upload_widget` (the footage
upload) is browser-side by provider design — there is no headless upload path, so a
headless run stops at the gate and asks rather than faking one.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-restyle",
    capability_tier=Tier.PRODUCTION,
    version="0.1.0",
    phase="11",
    description="Restyle the operator's own real footage (4-120s) into an on-brand short using Higgsfield's shorts_studio_create and the brand kit's restyle_preset_id (a look trained once by identity-kit from 1-20 brand reference files, then reused). Interactive-first by construction: the upload is media_upload_widget, browser-side by provider design, so a headless run stops at the gate and asks rather than faking an upload. Preflights the exact spend via get_cost + duration_seconds before generating, and states the 720p-only output constraint in its report rather than hiding it. This skill should be used when the user says 'restyle this video', 'apply the brand look to my footage', 'make this on-brand', or as the restyle stage of the creator pack's restyle-shorts variant.",
)
