"""Canonical manifest for the `video-avatar` skill.

Prompt body: plugin/skills/video-avatar/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.

**A new skill, deliberately NOT a branch inside `video-render`.** W7.2 retires `video-render` as a
presenter renderer, and that body is where every banned assumption lives — the `soul_2 -> outpaint
-> wan2_7 start_image` identity path, `audio_references` as a supposed lip-sync switch, prose
mouth direction. Grafting a working engine onto it would carry all of that forward under a name
that now looked validated. So the presenter lane gets its own body with its own rules.

**The one invariant this skill exists to hold.** It renders a synthetic likeness of a real person,
which is the single most regulated thing this repo produces. `gtm_core.render_engines` binds the
`presenter` role to an engine carrying `requires_disclosure = true`, so the render resolves ONLY
when the caller declares the tenant's `[disclosure].line` will ship with it — and the manifest it
writes records `lip_sync_source = "native_audio"`, a value `gtm_core.render_manifest` refuses over
any engine the registry does not mark `lip_sync = "native"`. Neither is advice in prose: both are
refusals in code, which is the whole lesson of the two videos rejected in August 2026.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-avatar",
    capability_tier=Tier.PIPELINE,
    version="0.5.0",
    phase="P4",
    description=(
        "Render the SPEAKING PRESENTER beats of a shot list as a synthetic talking head on "
        "HeyGen, the only engine the render-engine registry binds to the `presenter` role. This "
        "is the one lane `video-render` may not serve: that skill renders no presenter at all, by "
        "construction, because a trained Soul cannot cross the image-to-video boundary "
        "and a general image-to-video model animates the mouth from prose rather than from the "
        "audio. Resolves `identity.heygen_avatar_id` and `identity.heygen_voice_id` from the "
        "brand kit, refuses a shipped VO on anything but a `professional` voice clone "
        "(`identity.heygen_voice_grade`, unrecorded fails closed), starts the avatar LOOK from the "
        "one the operator APPROVED for the target orientation "
        "(`identity.heygen_look_landscape` / `identity.heygen_look_portrait`, never the other "
        "orientation's) and confirms it before spending, falling back to a source-resolution and "
        "orientation ranking only as the TIEBREAK when nothing is approved yet — a 720p landscape "
        "digital twin cropped to 4:5 starves the face of exactly the pixels the "
        "shot is about, but which look someone wants to be seen in is their call, not a "
        "geometry result — and uses `engine: avatar_v` with `reference_look_id` so a high-resolution "
        "portrait photo avatar borrows its motion from the twin. Renders the presenter's beats "
        "ONE PER BEAT, because HeyGen bills PER SECOND, not per render: measured twice against "
        "balance deltas on 2026-08-30 at 0.865 credits/second (206 credits over 12.72s of "
        "delivered footage), which is why the old '~23 credits per render, per minute rounded up' "
        "rule — and the one-continuous-take strategy built on it — is retired. It predicted 69 "
        "credits for a batch that cost 40. Under per-second billing a per-beat set costs the SAME "
        "as one take, needs no `split`, confines a failed render to one beat instead of the film, "
        "and lets a single line be re-rendered for the seconds it contains rather than re-cutting "
        "a whole take. Sizes each beat off the DELIVERED render rather than carrying the shot "
        "list's asked timing forward. A `<break time=…>` below 0.5s FAILS the render — 12 of 12 "
        "jobs agreed on 2026-08-30 (none and 0.5s completed; 0.4s and 0.35s failed, twice after a "
        "~30-minute hang) — so breaks are floored at 0.5s, and eight concurrent submissions "
        "serialise into ~16-minute queue waits where the same jobs solo return in 66–188s. A "
        "cutaway card's voice-over is NOT a render: `create_speech` returns the same professional "
        "clone as audio for ZERO credits with word-level timestamps — which are now PERSISTED beside the wav as `<id>.words.json` via `gtm_core.vo_timings ingest`, so a narration-cued card is animated against the voice that exists rather than against sixteen fractions someone transcribed by hand from a previous cut — so rendering a whole avatar "
        "video to harvest its audio track — roughly 80 credits a pass — is never the path. "
        "A motion prompt describes a PERSON, never a diagram: 'two fingers raised', 'one flat "
        "hand above the other to show two stacked layers' and their kind are refused, because the "
        "engine renders the metaphor literally and hands the presenter a prop (this shipped twice "
        "— counted fingers, then a wooden board). Counts and layers live in the graphics. Polls "
        "`get_video`, fetches the result "
        "through the host-pinned `gtm_core.media_fetch` (never a raw URL), logs cost to "
        "costs.jsonl BEFORE "
        "writing the manifest, and records `lip_sync_source = 'native_audio'` — a claim "
        "`gtm_core.render_manifest` refuses over any engine not marked `lip_sync = 'native'`. "
        "Carries the EU AI Act Article 50 disclosure by construction: the engine resolves only "
        "for a render that has declared the tenant's `[disclosure].line`, so an undisclosed "
        "synthetic presenter is unrepresentable here rather than merely discouraged. Never "
        "publishes and never renders b-roll or screen shots (those stay with `video-render` and "
        "the local Pillow lane). This skill should be used when the user says 'render the "
        "presenter shots', 'make the talking-head beats', 'render my avatar', or as the presenter "
        "half of a creator-pack render fan-out, before `video-finish`."
    ),
)
