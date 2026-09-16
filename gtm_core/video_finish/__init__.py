"""The finishing layer that has never existed as code. `grep -rl ffmpeg gtm_core/ agent/ tests/`
returned zero hits before this module — the one asset this system has ever shipped looks decent
only because of an untested, unversioned, brand-unaware script living in `content/` (state, not
code).

Phase A ships the minimal chain the one real asset needs:

    normalize → upscale 1080 → cuts → grade → captions → loudness → encode → lint → manifest

(F8's corrected stage order matters for exactly what ships here: a crop/reframe cut *before* the
caption burn keeps every recorded caption bbox valid; a grade *before* the burn means a brand-
colour check on the graded frame is honest, because it measures pixels that were never
composited with white caption glyphs.)

Phase E adds three additive, separately-callable pieces for the long-form case — none of them
touch `plan()`/`execute()`, which stay exactly as Phase A shipped them:

    stitch()          — join finished per-shot files (concat demuxer, re-encode only where a
                         shot was reframed) into one source BEFORE it reaches execute()
    duck_music_bed()  — sidechain-compress a music bed under a voice track into one AAC mix
    (cover-frame extraction lives in :mod:`gtm_core.cover_frame`, not here — it needs Pillow,
    which this module deliberately does not import; see that module's own boundary reasoning.)

THE PIPELINE IS A VALUE BEFORE IT IS A PROCESS
------------------------------------------------
:func:`plan` returns a :class:`FinishPlan` — an ordered tuple of :class:`Stage` plus a
:meth:`FinishPlan.census` — built with **no ffmpeg present and no filesystem writes**. The
single-grade invariant ("never grade twice") is checked inside `plan()` itself and is therefore
assertable in a pure unit test; the census is also written verbatim into `finish-<ratio>.json`,
so `assert plan(...).census()["grade"] == 1` is checkable from the artifact after the fact, not
just from a code review.

IDEMPOTENCY (run twice → byte-identical, captions never double-burned)
-------------------------------------------------------------------------
Three things, all required:

1. ``plan_id = sha256(canonical_json(plan))[:16]``. :func:`execute` short-circuits to **zero**
   ffmpeg invocations when the output already exists with a matching ``plan_id`` recorded in its
   sidecar ``finish-<ratio>.json``.
2. ``-fflags +bitexact -flags +bitexact -map_metadata -1`` plus a ``creation_time`` derived from
   ``plan_id`` (never wall-clock — an ``mvhd`` timestamp alone breaks byte-equality across runs).
3. Pinned ``-preset``/``-crf``/``-g``/``-threads N`` (``-threads 0`` makes output depend on the
   host's core count).

Every ffmpeg call is ``subprocess.run([...], check=True)`` — an **arg list**, never
``shell=True`` (bandit B602 is HIGH and scans this package). Intermediates land in
``workdir/*.part``; the finished file is placed via one atomic ``os.replace`` — a crash never
leaves a plausible-looking half-finished mp4 where a caller might mistake it for done.

FFMPEG ABSENT
-------------
:func:`plan` still succeeds — it is pure data and touches no subprocess. :func:`execute` renders
captions if the spec has any (rendering is Pillow, not ffmpeg — see :mod:`gtm_core.captions`),
writes ``finish-<ratio>.json`` with ``"executed": false`` and the full ordered stage list, then
raises :class:`FfmpegUnavailable` naming the missing binary. The CLI turns that into exit 3.
Never a silent partial output, never exit 0.

WHY THE CLI CARRIES mux/split/stitch AND NOT JUST run
-------------------------------------------------------
``Bash(ffmpeg:*)`` is DENIED in ``.claude/settings.json`` (2026-08-18) so a hand-authored filter
chain cannot bypass this module's grading / caption / loudness invariants. That denial covers the
whole binary, but only ``run`` (grade+caption+encode) had a CLI verb — leaving the multi-shot
render's other ffmpeg steps with no sanctioned path at all, which is how ``video-render``'s
prose ended up telling callers to shell out to raw ffmpeg for them. ``mux``, ``split`` and
``stitch`` close that gap: every ffmpeg invocation still originates inside ``gtm_core`` via
:func:`_run_ffmpeg` (arg list, never ``shell=True``), and each verb confines its output to the
resolved content root (:func:`_confined_output`, :func:`_confined_dir`) so the boundary is real
rather than nominal.

``split`` (2026-08-29) is the newest of the three and closes the gap the *last* fix opened.
``video-avatar`` Step 3 had reversed to one continuous presenter take on a per-job reading of
HeyGen billing, which made the presenter footage a single file while ``video-render``'s inserts
stayed separate files meant to be intercut with it. (That reading was retired 2026-09-07 —
HeyGen bills per second, ``gtm_core.heygen_cost`` — and the skill renders per beat again, so
``split`` is now an operator utility with no skill caller; see its docstring.) ``stitch`` only joins, so the creator pack's ``finish`` node
described an edit nothing could perform: cutting the take was denied at the Bash layer and absent
from the only sanctioned door. A cost fix in one file had broken assembly in another, and the two
never met.

CLI::

    uv run python -m gtm_core.video_finish run --profile P --slug S --ratio 9x16 \\
        --spec finish-spec.json [--dry-run] [--json]
    uv run python -m gtm_core.video_finish predictor-trim --in draft.mp4 --out draft-14s.mp4
    uv run python -m gtm_core.video_finish mux --video shot1.mp4 --audio vo1.mp3 \\
        --out muxed/shot1.mp4 [--atempo-max 1.15] [--json]
    uv run python -m gtm_core.video_finish split --in take-9x16.mp4 --cuts cuts.json \\
        --out-dir beats/ [--json]
    uv run python -m gtm_core.video_finish stitch --segments segments.json --ratio 4x5 \\
        --out joined.mp4 [--crossfade-s 0] [--json]
"""

from __future__ import annotations

from .audio import _db_envelope_expr, build_music_bed, duck_music_bed, room_tone  # noqa: F401
from .burn import BurnedShot, BurnResult, burn_captions  # noqa: F401
from .cli import main  # noqa: F401
from .confine import _confined_dir, _confined_output, _safe_asset_path  # noqa: F401
from .constants import (  # noqa: F401
    AUDIO_CHANNELS,
    AUDIO_SAMPLE_RATE,
    DEFAULT_CRF,
    DEFAULT_GOP,
    DEFAULT_LOUDNESS_LUFS,
    DEFAULT_PRESET,
    DEFAULT_THREADS,
    MUX_ATEMPO_MAX,
    MUX_ATEMPO_MAX_LIP_SYNCED,
    MUX_TRUNCATE_MAX_S,
    PREDICTOR_TRIM_S,
)

# Eager, complete re-export of the pre-split module surface (PRD §5 rule 1):
# every submodule is imported here, so module-level registrations run on
# `import <package>` exactly as they did on `import <module>`.
from .errors import FfmpegUnavailable, LintError, PlanError, PolishError, SfxError  # noqa: F401
from .execute import (  # noqa: F401
    FinishResult,
    _build_filtergraph,
    _carried_suppressions,
    _existing_plan_id,
    _identity_used_from_render,
    _write_manifest,
    _write_spec_sidecar,
    execute,
)
from .ffmpeg import (  # noqa: F401
    _MAX_VOLUME_RE,
    _N_SAMPLES_RE,
    _measure_window_dbfs,
    _parse_max_volume,
    _probe_dims,
    _probe_duration,
    _probe_fps,
    _probe_stream_kinds,
    _run_ffmpeg,
    _run_ffmpeg_capture,
)
from .mux import (  # noqa: F401
    MuxResult,
    _suggest_duration,
    frames_to_video,
    inset_video,
    mux,
    overlay_frames,
)
from .narration import (  # noqa: F401
    NARRATION_LEN_TOLERANCE_S,
    NarrationError,
    NarrationLine,
    NarrationLineMeasurement,
    NarrationTrackResult,
    _build_narration_filtergraph,
    _measure_narration_lines,
    build_narration_track,
    lines_from_shotlist,
)
from .plan import (  # noqa: F401
    _CAPTION_ROUTES,
    VENDOR_FALLBACK_REASONS,
    FinishPlan,
    Stage,
    _audio_context_from_spec,
    _canonical_json,
    _ratio_slug,
    _screens_for_caption_stage,
    plan,
    vendor_fallback_suppression,
)
from .polish import (  # noqa: F401
    GRADE_SIDECAR_SUFFIX,
    grade_shot,
    polish_with_reap,
    predictor_trim,
)
from .ratio import pad_to_ratio  # noqa: F401
from .sfx import (  # noqa: F401
    SFX_SHORTFALL_MAX_DB,
    _build_sfx_filtergraph,
    _cleanup_sfx,
    _sfx_format_chain,
    _validate_sfx_cues,
    mix_sfx_cues,
)
from .sfx_cues import SfxCue, SfxCueMeasurement, SfxMixResult  # noqa: F401
from .sfx_detect import (  # noqa: F401
    SFX_MIN_CUE_PEAK_DBFS,
    SFX_NULL_TRUST_MARGIN_DB,
    SFX_TRANSIENT_HOP_S,
    SFX_TRANSIENT_REL_FLOOR_DB,
    SFX_TRANSIENT_SEARCH_S,
    SFX_TRANSIENT_WINDOW_S,
    Transient,
    _control_window,
    _null_residual_dbfs,
    find_transient,
)
from .shots import ShotSegment, _normalize_shot  # noqa: F401
from .split import SPLIT_END_TOLERANCE_S, SplitResult, TakeCut, split  # noqa: F401
from .stills import ANIMATIC_FPS, still_to_segment  # noqa: F401
from .stitch import stitch  # noqa: F401

__all__ = [
    "still_to_segment",
    "ANIMATIC_FPS",
    "PREDICTOR_TRIM_S",
    "DEFAULT_CRF",
    "DEFAULT_PRESET",
    "DEFAULT_GOP",
    "DEFAULT_THREADS",
    "DEFAULT_LOUDNESS_LUFS",
    "AUDIO_SAMPLE_RATE",
    "AUDIO_CHANNELS",
    "MUX_ATEMPO_MAX",
    "MUX_ATEMPO_MAX_LIP_SYNCED",
    "MUX_TRUNCATE_MAX_S",
    "FfmpegUnavailable",
    "PlanError",
    "LintError",
    "PolishError",
    "Stage",
    "FinishPlan",
    "VENDOR_FALLBACK_REASONS",
    "vendor_fallback_suppression",
    "plan",
    "FinishResult",
    "execute",
    "ShotSegment",
    "MuxResult",
    "mux",
    "frames_to_video",
    "overlay_frames",
    "inset_video",
    "SPLIT_END_TOLERANCE_S",
    "TakeCut",
    "SplitResult",
    "split",
    "stitch",
    "BurnedShot",
    "BurnResult",
    "burn_captions",
    "duck_music_bed",
    "SFX_SHORTFALL_MAX_DB",
    "SFX_TRANSIENT_WINDOW_S",
    "SFX_TRANSIENT_HOP_S",
    "SFX_TRANSIENT_SEARCH_S",
    "SFX_TRANSIENT_REL_FLOOR_DB",
    "SFX_MIN_CUE_PEAK_DBFS",
    "SFX_NULL_TRUST_MARGIN_DB",
    "SfxError",
    "Transient",
    "SfxCue",
    "SfxCueMeasurement",
    "SfxMixResult",
    "find_transient",
    "mix_sfx_cues",
    "pad_to_ratio",
    "build_music_bed",
    "room_tone",
    "polish_with_reap",
    "predictor_trim",
    "main",
]
