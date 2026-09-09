"""Canonical manifest for the `video-avatar` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/video-avatar/SKILL.md``.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-avatar",
    capability_tier=Tier.PRODUCTION,
    version="0.6.0",
    phase="P4",
    description=(
        "Render the SPEAKING PRESENTER beats of a shot list as a synthetic talking head on HeyGen, the "
        "only engine the render-engine registry binds to the `presenter` role. This is the one lane "
        "`video-render` may not serve — that skill renders no presenter at all, by construction. "
        "Resolves `identity.heygen_avatar_id` and `identity.heygen_voice_id` from the brand kit, "
        "refuses a shipped VO on anything but a `professional` voice clone "
        "(`identity.heygen_voice_grade`, unrecorded fails closed), and starts the avatar LOOK from the "
        "one the operator APPROVED for the target orientation (`identity.heygen_look_landscape` / "
        "`identity.heygen_look_portrait`, never the other orientation's), confirming it before "
        "spending. A source-resolution and orientation ranking is the TIEBREAK only: which look "
        "someone wants to be seen in is their call, not a geometry result. Uses `engine: avatar_v` "
        "with `reference_look_id` so a portrait photo avatar borrows its motion from the twin. Renders "
        "ONE clip per beat: HeyGen bills per second of delivered footage, so a per-beat set costs the "
        "same as one continuous take and confines a failed render to one beat. Sizes each beat off the "
        "DELIVERED render rather than carrying the shot list's asked timing forward. The rate is owned "
        "by `gtm_core.heygen_cost` and stated nowhere else. Floors `<break time=…>` at 0.5s — below "
        "that the render fails outright. A cutaway card's voice-over is NOT a render: `create_speech` "
        "returns the same professional clone as audio for ZERO credits with word-level timestamps, "
        "persisted beside the wav as `<id>.words.json` via `gtm_core.vo_timings ingest`. A motion "
        "prompt describes a PERSON, never a diagram: 'two fingers raised' or 'one flat hand above the "
        "other to show two stacked layers' are refused, because the engine renders the metaphor "
        "literally and hands the presenter a prop; counts and layers live in the graphics. Polls "
        "`get_video`, fetches the result through the host-pinned `gtm_core.media_fetch` (never a raw "
        "URL), logs cost to costs.jsonl BEFORE writing the manifest, and records `lip_sync_source = "
        "'native_audio'` — a claim `gtm_core.render_manifest` refuses over any engine not marked "
        "`lip_sync = 'native'`. Carries the EU AI Act Article 50 disclosure by construction: the "
        "engine resolves only for a render that has declared the tenant's `[disclosure].line`, so an "
        "undisclosed synthetic presenter is unrepresentable here. Never publishes, and never renders "
        "b-roll or screen shots (those stay with `video-render` and the local Pillow lane). This skill "
        "should be used when the user says 'render the presenter shots', 'make the talking-head "
        "beats', 'render my avatar', or as the presenter half of a creator-pack render fan-out, before "
        "`video-finish`."
    ),
)
