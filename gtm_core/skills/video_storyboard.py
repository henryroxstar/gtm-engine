"""Canonical manifest for the `video-storyboard` skill.

Prompt body: plugin/skills/video-storyboard/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.

This is the pre-spend review gate of the creator pack's short-form-video variant.
It runs after `video-script` and before `video-render`, generating operator-reviewable
still(s) using the same identity anchor `video-render` Step 1 resolves. The approved
still(s) are written to `storyboard.json` and consumed directly by `video-render` as
the image-to-video start frame, so the frame the operator approves is the frame that
gets animated. For single-clip scripts this means one still; for multi-shot long-form
scripts it means one still per presenter shot (skipping `role=broll/screen` shots).
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-storyboard",
    capability_tier=Tier.PRODUCTION,
    version="0.3.0",
    phase="6",
    description=(
        "Generate operator-reviewable storyboard still(s) for an approved video script "
        "before any video render spend. Reads the approved script "
        "(`content/<active>/scripts/<YYYY-MM-DD>-<slug>.md`) and optional `<slug>.shots.json`, "
        "resolves the same identity anchor as `video-render` via the brand kit "
        "(`python -m gtm_core.brandkit --profile <active> [--product <slug>]`), runs the "
        "monthly-cap precheck (`python -m gtm_core.ledger_cli month-total`), then generates "
        "ONE hero still for the whole video with `generate_image` — never one per shot: every "
        "independent still re-invents wardrobe, lighting and setting, so N stills render as N "
        "unexplained costume changes (verified 2026-08-18: five stills, five different jackets "
        "in 32 seconds). A second hero still is generated only for a deliberate look change, and "
        "`gtm_core.storyboard approve` refuses more than one distinct image_job_id without an "
        "explicit `--allow-anchors N --reason`. Always folds the shot's `expression` field into "
        "the prompt when present — an unstated "
        "expression defaults to whatever the identity anchor's source photo happens to hold, "
        "which is the still-generation half of the same 'prompt text, not luck' lesson wardrobe "
        "already carries. Writes "
        "`content/<active>/video/<script-slug>/storyboard.json` recording shot number, local "
        "image path, provider job/media id, identity anchor kind, aspect ratio, width, and "
        "height. Ends its turn with the `⟦GATE:plan⟧` sentinel so the pack's frontier pauses "
        "for operator approval. On approval, `video-render` reads the approved storyboard and "
        "uses those stills directly as the image-to-video start frame instead of generating "
        "new ones. This skill should be used as the `storyboard` node of the creator pack's "
        "`short-form-video` variant, or when the user says 'storyboard this script', 'show me "
        "the video frames before render', or 'preview the shot composition'."
    ),
)
