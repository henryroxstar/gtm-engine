"""Gate a finished video asset on the defects a rubric score cannot see.

The one asset this system has ever shipped scored 14/14 on the retention rubric's Part A while
being 720x1280 at 24fps/1.42 Mbps, having a caption clipped at both frame edges, and running
15.041667s into a predictor that hard-fails past ~15s — 4/4 observed scoring failures. No existing
gate had an opinion about the *artifact*; Part A is an opinion about the treatment.

Modelled on gtm_core/deck_lint.py, with its two known gaps closed for a binary artifact:

  - deck_lint's suppression comment (``<!-- lint-ok D4: reason -->``) lives INSIDE the linted
    text. An .mp4 has no comment syntax, so a suppression here lives in the sidecar JSON the
    producer (gtm_core.video_finish) already writes, as ``lint_suppressions``.
  - deck_lint never enforces a suppression REASON and never counts/reports suppressions. Both are
    fixed here: an empty/placeholder reason is a malformed input (exit 2, not a quiet pass), and
    "suppressed: N" is always printed, even at zero — success criterion §7 #4 ("% clearing V1-V4
    with zero ERRORs AND NO SUPPRESSION") is uncomputable otherwise.

Six tiers ship, each with an OBSERVED defect behind it (not a guess dressed as an ERROR gate —
see the ``evidence`` on each ``Tier`` and the promotion invariant enforced at import, below):

    V1  delivery floor    — resolution/fps/bitrate below the professional floor        ERROR
    V2  aspect exact      — frame dims must match the requested ratio, not just be     ERROR
                             close (catches the soul_2 4:5->3:4 coercion)
    V3  caption geometry  — no caption glyph within the reserved safe-area margin,     ERROR/WARN
                             and none inside the ratio's face band
    V4  predictor input   — duration <= 14.9s, ONLY when purpose="predictor"           ERROR
    V10 dead air          — measured silence, not stream presence                      ERROR
    V11 caption contrast  — burned type clears WCAG AA against its own backdrop        ERROR

V10 and V11 (2026-08-28) close the gap three-questions-p1 walked through: it passed every tier
above while 39% of its runtime was digital silence. ``Probe.has_audio`` had been captured since
V1 shipped and read by no rule — and was itself wrong, because ``probe()`` passed
``-select_streams v:0`` and never saw the audio stream at all. Both are fixed here.

``probe()`` is the only ffprobe shell-out. ``evaluate()`` is pure — it takes a ``Probe`` (real or
fabricated) plus optional context (a caption manifest, edge-luma stats, the intended purpose) and
returns findings with no I/O. That split is what makes tier-B tests honest: boundary cases can be
asserted against a fabricated ``Probe`` with no ffmpeg on the machine at all, and it is what makes
the calibration test possible — a committed, de-identified ``ffprobe`` JSON measurement can stand
in for a real (gitignored, likeness-bearing) asset.

V1 is really two sub-checks under one tier name, on purpose: resolution/fps is fixture-provable at
200ms; bitrate is not (a short CBR clip's measured bitrate is dominated by its I-frame), so the
bitrate sub-check is honestly provable only via the pure layer plus a real-asset calibration.

V3 has two inputs and only one of them is pixels. Primary (ERROR): a caption manifest's per-screen
bounding box vs. the reserved safe-area margin, computed from the PROBED frame dimensions — pure
arithmetic, exact, no pixel-guessing. Fallback (WARN): edge-strip luma stats for any asset with no
manifest (i.e. every asset produced before this program) — a WARN, and it says why it degraded.

V4 is scoped by ``purpose``, not by asset: "the asset is <=14.9s" is the obvious reading of the
PRD and it is wrong — a long-form asset is legitimately 90s. V4 fires only when the caller is
about to hand the file to the predictor (``purpose="predictor"``).

Suppression syntax: ``lint_suppressions: [{"tier": "V3", "asset": "<filename>", "reason": "..."}]``
in the producer's manifest. A tier name is matched by EQUALITY against ``Tier.id`` (never
substring), which by itself makes a "V10 read as V1" collision structurally impossible — the
anchored ``_TIER_RE`` below still exists as a second, independent line of defence on top of that
equality check, for whatever future code reads a suppression's tier field back out of the report.

Promotion is mechanical, not editorial: a Tier cannot be constructed with ``severity=ERROR`` and
fewer than two ``evidence`` entries (checked at import). A WARN candidate tier earns ERROR only
after it has caught a real defect on two separate assets, named here.

CLI:
    uv run python -m gtm_core.video_lint asset.mp4 --ratio 9x16
    uv run python -m gtm_core.video_lint asset.mp4 --ratio 9x16 --purpose predictor --json
    uv run python -m gtm_core.video_lint asset.mp4 --ratio 9x16 --manifest finish-9x16.json
"""

from __future__ import annotations

from .cadence import bed_findings, cadence_findings  # noqa: F401
from .cli import main, report  # noqa: F401
from .evaluate import _face_band_fix, _screens_phrase, evaluate  # noqa: F401
from .measure import (  # noqa: F401
    CONTRAST_SAMPLE_LIMIT,
    _last_float,
    _safe_box,
    classify_boundaries,
    frame_deltas,
    measure_audio,
    measure_caption_contrast,
    measure_cuts_and_motion,
    measure_transitions,
)

# Eager, complete re-export of the pre-split module surface (PRD §5 rule 1):
# every submodule is imported here, so module-level registrations run on
# `import <package>` exactly as they did on `import <module>`.
from .model import (  # noqa: F401
    _DEFAULT_FACE_BAND,
    _TIER_RE,
    ERROR,
    FACE_BANDS,
    SAFE_AREAS,
    WARN,
    Finding,
    SafeArea,
    Tier,
    face_band,
)
from .probe import Probe, ProbeFailed, ProbeUnavailable, ffmpeg_available, probe  # noqa: F401
from .suppress import (  # noqa: F401
    _MIN_SUPPRESSION_REASON_CHARS,
    _PLACEHOLDER_REASONS,
    BadSuppression,
    Suppression,
    _validate_suppressions,
    apply_suppressions,
    stale_suppressions,
)
from .thresholds import (  # noqa: F401
    ASPECT_TOLERANCE,
    MAX_DEAD_AIR_FRACTION,
    MAX_DEAD_AIR_RUN_S,
    MAX_SECONDS_WITHOUT_CHANGE,
    MAX_TRANSITIONS_PER_MINUTE,
    MAX_WORDS_PER_SEC_ASSET,
    MAX_WORDS_PER_SEC_SCREEN,
    MIN_BITRATE_1080P_BPS,
    MIN_CAPTION_CONTRAST_RATIO,
    MIN_FPS_ACCEPTABLE,
    MIN_FPS_CLEAN,
    MIN_SHOT_MOTION,
    PREDICTOR_MAX_DURATION_S,
    SCENE_CHANGE_THRESHOLD,
    SILENCE_FLOOR_DBFS,
    SILENCE_MIN_RUN_S,
    TRANSITION_MIN_BLEND_FRAMES,
    V5_BED_SEPARATION_DB,
    V6_MIN_AVG_CUT_INTERVAL_S,
    V6_NO_CHANGE_MIN_DURATION_S,
    V6_SPARSE_WINDOW_S,
)
from .tiers import _TIERS_BY_ID, CANDIDATES, SHIPPED  # noqa: F401

__all__ = [
    "frame_deltas",
    "classify_boundaries",
    "measure_transitions",
    "bed_findings",
    "cadence_findings",
    "SCENE_CHANGE_THRESHOLD",
    "TRANSITION_MIN_BLEND_FRAMES",
    "MAX_TRANSITIONS_PER_MINUTE",
    "MAX_SECONDS_WITHOUT_CHANGE",
    "ERROR",
    "WARN",
    "Tier",
    "Finding",
    "SafeArea",
    "SAFE_AREAS",
    "MIN_BITRATE_1080P_BPS",
    "MIN_FPS_ACCEPTABLE",
    "MIN_FPS_CLEAN",
    "ASPECT_TOLERANCE",
    "PREDICTOR_MAX_DURATION_S",
    "MAX_WORDS_PER_SEC_SCREEN",
    "MAX_WORDS_PER_SEC_ASSET",
    "FACE_BANDS",
    "face_band",
    "SILENCE_FLOOR_DBFS",
    "SILENCE_MIN_RUN_S",
    "MAX_DEAD_AIR_FRACTION",
    "MAX_DEAD_AIR_RUN_S",
    "MIN_CAPTION_CONTRAST_RATIO",
    "MIN_SHOT_MOTION",
    "SHIPPED",
    "CANDIDATES",
    "ProbeUnavailable",
    "ProbeFailed",
    "BadSuppression",
    "Probe",
    "probe",
    "ffmpeg_available",
    "Suppression",
    "measure_cuts_and_motion",
    "measure_audio",
    "CONTRAST_SAMPLE_LIMIT",
    "measure_caption_contrast",
    "evaluate",
    "apply_suppressions",
    "stale_suppressions",
    "report",
    "main",
]
