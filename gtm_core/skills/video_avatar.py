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
    version="0.3.0",
    phase="P4",
    description=(
        "Render the SPEAKING PRESENTER beats of a shot list as a synthetic talking head on "
        "HeyGen, the only engine the render-engine registry binds to the `presenter` role. This "
        "is the one lane `video-render` may not serve: that skill leaves the presenter role "
        "unbound by construction, because a trained Soul cannot cross the image-to-video boundary "
        "and a general image-to-video model animates the mouth from prose rather than from the "
        "audio. Resolves `identity.heygen_avatar_id` and `identity.heygen_voice_id` from the "
        "brand kit, refuses a shipped VO on anything but a `professional` voice clone "
        "(`identity.heygen_voice_grade`, unrecorded fails closed), picks the avatar LOOK by "
        "source resolution and orientation against the target ratio rather than by name — a "
        "720p landscape digital twin cropped to 4:5 starves the face of exactly the pixels the "
        "shot is about — and uses `engine: avatar_v` with `reference_look_id` so a high-resolution "
        "portrait photo avatar borrows its motion from the twin. Renders one clip per presenter "
        "shot at its real spoken duration, polls `get_video`, fetches the result through the "
        "host-pinned `gtm_core.media_fetch` (never a raw URL), logs cost to costs.jsonl BEFORE "
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
