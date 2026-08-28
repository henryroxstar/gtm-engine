"""Canonical manifest for the `video-router` skill.

Prompt body: plugin/skills/video-router/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.

This is a thin front-door skill invoked before any creator-pack plan node. It asks one
plain-English question and dispatches to the correct creator pack variant, so a non-technical
operator never needs to know the four `.toml` filenames exist. It does not create a graph node
and does not itself spend credits — the Step 0 identity preflight it now runs is a read-only
brand-kit/provider liveness check, same shape as `identity-kit` Step 1, not a create call.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-router",
    capability_tier=Tier.CORE,
    version="0.4.0",
    phase="6",
    description=(
        "A thin front-door skill that routes a 'make a video' request into the correct creator "
        "pack variant. First runs a zero-spend identity preflight — audits the active profile's "
        "soul_id/voice_id liveness via the brand kit, and hands off to `identity-kit` before "
        "routing if a requested identity handle is missing, stale, or failed-training, since that "
        "is knowable before any script exists. (Reference-element gaps are NOT preflighted here — "
        "which non-person B-roll a video needs depends on the shot list, so that check belongs at "
        "`video-script`'s shot-role tagging or `video-storyboard`'s per-shot identity resolution.) "
        "Also reports WHICH presenter engine exists and on what condition "
        "(`python -m gtm_core.render_engines --role presenter --speaks [--disclosed]`) — a "
        "DISCLOSED synthetic talking head resolves to HeyGen and is rendered by `video-avatar`, "
        "while an UNDISCLOSED one is still refused pending the pre-registered panel in "
        "`gtm_core/panel_eval.py`. So the router surfaces all three lanes — real footage "
        "(preferred, no Article 50 duty), disclosed avatar, faceless — and makes the disclosure "
        "trade an explicit operator decision INSTEAD of routing into a script that would be "
        "refused at render time or, worse, defaulting to synthetic because it happens to resolve. "
        "Then asks one plain-English question — 'Do you have footage already, or should I "
        "write something from scratch?', footage first because it is the primary lane — and dispatches to `packs/creator/graphs/short-form-video.toml` "
        "for a from-scratch script, `repurpose-clips.toml` for long-form footage to clip, "
        "`restyle-shorts.toml` for footage to restyle, or `demo-clips.toml` for a screen "
        "recording / demo. Does not create a graph node; it runs before pack selection and emits "
        "a directive to start the correct variant. Unknown intent does not guess — it re-asks "
        "the one question. This skill should be used when the user says 'make a video', "
        "'create a video', 'I want a reel', 'turn this into a short', 'make a short-form video', "
        "or any similar request that does not already name a specific creator-pack lane."
    ),
)
