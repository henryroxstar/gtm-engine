"""Canonical manifest for the `video-finish` skill.

Phase A adds the `finish` node this graph shape was missing. The finishing layer
(`gtm_core.video_finish`, `gtm_core.captions`, `gtm_core.video_lint`) shipped as tested code with
no skill or pack node ever invoking it — every rendered asset skipped straight to scoring the raw,
unfinished render. This manifest + its thin body close that gap. Prompt body:
plugin/skills/video-finish/body_template.md (verbatim). SKILL.md is generated from this manifest by
gtm_core.skills.codegen.

**Its own node, before `score`, not a tail step of `video-render`.** `render-vertical` and
`render-feed` are two sibling nodes sharing one skill; a finishing pass over EACH of their
outputs is one more fan-in step, not folded into the render skill itself, so a re-render never
implicitly re-finishes (or vice versa) and `video_lint` always gates the artifact that is
actually a candidate for scoring/shipping, not the provider's raw output.

**The body is thin by design — it shells out and reports.** The mechanism is code
(`gtm_core.video_finish`/`gtm_core.video_lint`/`gtm_core.captions`); the skill is the invocation.
No filter chain, no drawtext, no second grade pass are ever authored in the prompt — the module
owns both and asserts the single-grade invariant itself (`FinishPlan.plan().census()["grade"]`).
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-finish",
    capability_tier=Tier.PIPELINE,
    version="0.4.0",
    phase="A",
    description=(
        "Finish a rendered short-form video variant into a shippable artifact: normalize, "
        "upscale to the target frame, grade exactly once, burn captions inside the reserved "
        "safe area (Pillow-rendered PNG overlays, never ffmpeg drawtext/ass), loudness-normalize "
        "to -14 LUFS, and encode — all via `python -m gtm_core.video_finish run`, never a "
        "hand-authored ffmpeg filter chain. Routes captions through Reap's `add_captions` with the "
        "tenant's declared `captions.preset` when one resolves — timed from `transcribe`'s real "
        "per-word timings rather than even apportionment — and REFUSES at plan time to burn "
        "locally against a resolving preset unless the spec records a `caption_route_suppression` "
        "reason, because that exact bypass shipped 24 caption screens over the speaker's face "
        "while the Reap plan sat at 0 of 600 credits used. Runs `python -m gtm_core.video_lint` "
        "on the result and reports any V1-V4 finding rather than shipping a defect silently. "
        "Writes "
        "finish-<ratio>.json (gtm_core.render_manifest.FinishManifest) — the file "
        "gtm_core.outcomes and video-score both read back. Never re-renders and never publishes; "
        'this skill should be used when the user says "finish the render", "burn captions", '
        '"grade and encode the video", or as the finish stage of the creator pack, between '
        "render and score."
    ),
)
