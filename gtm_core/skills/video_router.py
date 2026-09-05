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
    version="0.7.0",
    phase="6",
    description=(
        "A thin front-door skill that routes a 'make a video' request into the correct creator "
        "pack variant. First runs a zero-spend, zero-write lane preflight "
        "(`python -m gtm_core.video_preflight --profile <active> [--product <slug>]`) that "
        "resolves from disk WHICH LANES CAN ACTUALLY RUN and what bounds them: lane existence, "
        "pack activation, the identity handles THAT LANE needs, engine resolution, disclosure "
        "line, the kit's imagery constraints, caption preset, shot-mix ceiling, and the reusable "
        "assets the profile already holds. The handle requirement is keyed to the LANE, not to a "
        "fixed pair: `presenter-video` renders on HeyGen and needs heygen_avatar_id + "
        "heygen_voice_grade, so auditing soul_id/voice_id says nothing about whether it can run. "
        "(Reference-element gaps are NOT preflighted — which non-person B-roll a video needs "
        "depends on the shot list, so that check belongs at `video-script`'s shot-role tagging or "
        "`video-storyboard`'s per-shot identity resolution.) It reports WHICH presenter engine "
        "exists and on what condition (`python -m gtm_core.render_engines --role presenter "
        "--speaks [--disclosed]`) — a DISCLOSED synthetic talking head resolves to HeyGen and is "
        "rendered by `video-avatar`, while an UNDISCLOSED one is still refused pending the "
        "pre-registered panel in `gtm_core/panel_eval.py` — so the disclosure trade stays an "
        "explicit operator decision instead of a default taken because it happens to resolve. "
        "It also surfaces the operator's APPROVED AVATAR LOOK per orientation "
        "(identity.heygen_look_landscape / identity.heygen_look_portrait) as a Step 0.5 DECISION "
        "and ASKS ABOUT IT EVERY RUN, before any spend — a recorded look is a proposal answered "
        "with a one-line confirm naming the look and its native pixels, never a silent default, "
        "and an unrecorded one is answered with the candidate list for that orientation rather "
        "than a pick made on the operator's behalf. A lane is never blocked for lacking one: it "
        "is a decision, not a gate. "
        "Then, instead of asking a question whose answers it has not verified, it offers the "
        "lanes the preflight marked READY, naming each blocked lane's single unlock step, and "
        "dispatches to `packs/creator/graphs/short-form-video.toml` for a from-scratch faceless "
        "script, `presenter-video.toml` for a disclosed synthetic presenter cut against inserts, "
        "`repurpose-clips.toml` for long-form footage to clip, `restyle-shorts.toml` for footage "
        "to restyle, `demo-clips.toml` for a screen recording / demo, or "
        "`live-action-video.toml` for write-it-then-I-shoot-it — the lane that writes the script "
        "first and PARKS at a capture gate until the operator has filmed it, with no render nodes "
        "at all and so no EU AI Act Art. 50 disclosure duty, which is what makes real footage the "
        "preferred lane for anything showing a face. `short-form-video` needs "
        "no identity handles and no footage, so it is the cold-start default for a profile that "
        "has neither; identity is progressive rather than front-loaded. Does not create a graph "
        "node; it runs before pack selection and emits a directive to start the correct variant. "
        "Unknown intent does not guess, and a request matching NO lane is reported as a gap "
        "rather than bent into the nearest one. This skill should be used when the user says "
        "'make a video', 'create a video', 'I want a reel', 'turn this into a short', 'make a "
        "short-form video', or any similar request that does not already name a specific "
        "creator-pack lane."
    ),
)
