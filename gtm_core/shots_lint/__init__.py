"""Shot-list linter — deterministic craft checks on a ``<slug>.shots.json`` before render spends.

Phase 18 (R5/R6): mechanizes the two prompt-craft rules every current video model's own guide
agrees on, which previously lived only as prose in the ``video-script`` skill body:

* **One camera move per shot.** Stacked moves ("dolly in while zooming and panning left") are the
  documented cause of warped geometry and unstable framing on Kling, Hailuo, Runway, and Sora
  alike. A named compound ("dolly zoom", "whip pan") is one move, not two.
* **Positive phrasing in prompt fields.** Negation ("no movement", "without a logo") is a
  documented failure mode — the banned noun stays "in play". Exclusions belong in
  ``style_scaffold.negative`` (plain nouns) or the shot's ``stability`` clause, stated
  positively. ``spoken`` (dialogue for TTS) and ``negative`` (the designated exclusion field)
  are exempt.

Errors (exit 1) block hand-off; warnings are advisory (exit 0): an unrecognized camera term, a
``duration_s`` past every confirmed provider ceiling, and a beat-total drift from
``total_duration_s``. Structural requirements mirror ``schemas/shots.schema.json`` minimally —
the schema contract itself is enforced by tests; this CLI has no third-party deps by design.

Run by ``video-script`` at Step 1.5 hand-off (free, no spend)::

    uv run python -m gtm_core.shots_lint content/<profile>/scripts/<date>-<slug>.shots.json
"""

from __future__ import annotations

from .api import _active_constants, active_rules, lint_shotlist  # noqa: F401
from .audio import _AUDIO_BED_FIELD, _MIN_SILENCE_REASON_CHARS, _lint_audio_bed  # noqa: F401

# Eager, complete re-export of the pre-split module surface (PRD §5 rule 1):
# every submodule is imported here, so module-level registrations run on
# `import <package>` exactly as they did on `import <module>`.
from .camera import (  # noqa: F401
    _COMPOUND_MOVES,
    _FRAMING_TERMS,
    _GESTURE_AS_DIAGRAM,
    _NEGATION_RE,
    _NEGATION_SCANNED_SHOT_FIELDS,
    _SIMPLE_MOVES,
    _lint_camera,
    _lint_gesture_is_not_a_diagram,
    _lint_motion_prompt,
    _lint_negation,
    _move_families,
    _normalize,
)
from .cli import main  # noqa: F401
from .identity import (  # noqa: F401
    _BINDINGS_NOTES_KEY,
    _IDENTITY_HANDLE_FIELDS,
    _RATIO_ORIENTATION,
    _lint_identity_bindings,
    _lint_look_ratio,
    _lint_presenter_engine,
    _lint_synthetic_disclosure,
    _lint_voice_grade,
    _orientation_of,
)
from .mix import (  # noqa: F401
    _IN_FRAME_TEXT_RE,
    _IN_FRAME_TEXT_SCANNED_FIELDS,
    _PRESENTER_QUOTA_MIN_SHOTS,
    MAX_PRESENTER_SHARE,
    _lint_no_in_frame_text,
    _lint_shot_mix,
)
from .narration import (  # noqa: F401
    _NARRATION_DUTY_WARN_PCT,
    _NARRATION_FIELD,
    _NARRATION_TOLERANCE_S,
    _lint_narration_duty_cycle,
    _lint_narration_lane_exclusive,
    _lint_narration_timeline,
    _well_formed_lines,
)
from .timing import (  # noqa: F401
    _CAPTURE_MODES,
    _DEFAULT_CAPTURE_MODE,
    _DURATION_DRIFT_TOLERANCE_S,
    _MAX_CONFIRMED_CEILING_S,
    _MIN_PRACTICAL_DURATION_S,
    _SPEECH_CUES,
    _VALID_ROLES,
    _VO_DEAD_AIR_TOLERANCE_S,
    _VO_PARITY_TOLERANCE_S,
    _VO_SOURCE_FIELD,
    PROVIDER_RUSHED_WORDS_PER_SEC,
    VO_WORDS_PER_SEC,
    VO_WORDS_PER_SEC_FAST,
    VO_WORDS_PER_SEC_SLOW,
    _estimated_vo_seconds,
    _lint_speech_cue,
    _lint_spoken_has_a_voice,
    _lint_vo_duration_parity,
    _lint_vo_measured_parity,
)

__all__ = [
    "VO_WORDS_PER_SEC",
    "VO_WORDS_PER_SEC_SLOW",
    "VO_WORDS_PER_SEC_FAST",
    "PROVIDER_RUSHED_WORDS_PER_SEC",
    "MAX_PRESENTER_SHARE",
    "lint_shotlist",
    "active_rules",
    "main",
]
