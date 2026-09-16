from __future__ import annotations

import re
import subprocess  # nosec B404 — ffprobe orchestration, arg list only, never shell=True
from pathlib import Path

from . import thresholds
from .model import SafeArea
from .thresholds import SILENCE_FLOOR_DBFS, SILENCE_MIN_RUN_S


def _safe_box(area: SafeArea, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) of the region a caption glyph may occupy, in pixels, computed from the
    ACTUAL probed frame dimensions (never the nominal target) — so a stale sidecar against a
    rescaled video is caught by a mismatched frame size, not silently trusted."""
    x0 = round(area.left * frame_w)
    x1 = frame_w - round(area.right * frame_w)
    y0 = round(area.top * frame_h)
    y1 = frame_h - round(area.bottom * frame_h)
    return x0, y0, x1, y1


def frame_deltas(path: Path) -> list[tuple[float, float]] | None:
    """Impure. ONE ffmpeg pass: per-frame ``(pts_time, mean inter-frame delta 0-1)``.

    ``tblend=all_mode=difference`` turns each frame into its delta from the previous one, and
    ``signalstats`` reports that delta's average luma (YAVG, 0-255). This is the signal both V9
    (per-shot motion) and V12 (multi-frame blends) are read from, so it is computed once and
    handed to both — the tblend pass is the most expensive analysis pass there is (full decode,
    full-frame difference, single-threaded filter), and running it twice bought nothing.
    """
    import shutil

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        return None
    try:
        out = subprocess.run(  # nosec B603 — arg list resolved via shutil.which, never shell=True
            [
                ffmpeg_bin,
                "-v",
                "info",
                "-i",
                str(path),
                "-vf",
                "tblend=all_mode=difference,signalstats,metadata=print:file=-",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    samples: list[tuple[float, float]] = []
    pending_t: float | None = None
    for line in (out.stdout or "").splitlines():
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            pending_t = float(m.group(1))
            continue
        m = re.search(r"lavfi\.signalstats\.YAVG=([0-9.]+)", line)
        if m and pending_t is not None:
            samples.append((pending_t, float(m.group(1)) / 255.0))
            pending_t = None
    return samples or None


def measure_cuts_and_motion(
    path: Path, *, deltas: list[tuple[float, float]] | None = None
) -> tuple[list[float] | None, list[dict] | None]:
    """Impure. One ffmpeg pass that yields both V6's cut list and V9's per-shot motion.

    ``tblend=all_mode=difference`` turns each frame into its delta from the previous one, and
    ``signalstats`` then reports that delta's average luma (YAVG, 0-255). Averaged over a shot
    and normalised to 0-1, it is a serviceable "does anything move here" measure. Returns
    ``(None, None)`` if ffmpeg is missing or the pass fails — the caller degrades to the checks
    that do not need it rather than reporting a clean asset it could not measure."""
    import shutil

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        return None, None
    try:
        out = subprocess.run(  # nosec B603 — arg list resolved via shutil.which, never shell=True
            [
                ffmpeg_bin,
                "-v",
                "info",
                "-i",
                str(path),
                "-vf",
                f"select='gt(scene,{thresholds.SCENE_CHANGE_THRESHOLD})',metadata=print:file=-",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    cuts = [float(m) for m in re.findall(r"pts_time:([0-9.]+)", out.stdout or "")]

    samples = deltas if deltas is not None else frame_deltas(path)
    if not samples:
        return (cuts or None), None

    bounds = [0.0] + sorted(cuts) + [samples[-1][0] + 1e-6]
    motion: list[dict] = []
    for i in range(len(bounds) - 1):
        lo, hi = bounds[i], bounds[i + 1]
        # skip the first sample after a cut: its delta is against the previous SHOT, not motion
        vals = [v for t, v in samples if lo < t < hi]
        if len(vals) > 1:
            vals = vals[1:]
        if not vals:
            continue
        motion.append({"index": i, "start": lo, "end": hi, "motion": sum(vals) / len(vals)})
    return (cuts or None), (motion or None)


def measure_audio(path: Path, *, duration_s: float | None = None) -> dict | None:
    """Impure. One ffmpeg pass yielding the MEASURED half of V5/V10's audio context.

    ``silencedetect`` reports every run at or below :data:`SILENCE_FLOOR_DBFS` lasting at least
    :data:`SILENCE_MIN_RUN_S`; ``ebur128`` reports integrated loudness and true peak in the same
    pass. Returns ``None`` if ffmpeg is missing or the pass fails — the caller degrades to the
    checks that do not need it rather than reporting a clean asset it could not measure.

    Only measurable facts come back. Whether a music bed exists, and whether it was ducked, are
    properties of the MIX that no analysis of the finished mono-sum can recover; those are
    declared by the producer in the finish sidecar and merged in by the caller."""
    log = _audio_pass_log(path)
    if log is None:
        return None
    return parse_audio_log(log, duration_s=duration_s)


def _audio_pass_log(path: Path) -> str | None:
    """Impure. Run the one silencedetect+ebur128 pass and return ffmpeg's diagnostic log, or
    ``None`` when ffmpeg is missing or the pass could not run. Split from :func:`measure_audio`
    so the parser is pure and can be tested on a captured log with no media on the machine."""
    import shutil

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        return None
    try:
        out = subprocess.run(  # nosec B603 — arg list resolved via shutil.which, never shell=True
            [
                ffmpeg_bin,
                "-v",
                "info",
                "-i",
                str(path),
                "-af",
                f"silencedetect=noise={SILENCE_FLOOR_DBFS}dB:d={SILENCE_MIN_RUN_S},"
                "ebur128=peak=true",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # ffmpeg writes filter diagnostics to stderr.
    return (out.stderr or "") + (out.stdout or "")


#: One ebur128 progress line per 100ms: ``t: 1.2  TARGET:-23 LUFS  M: -13.9 S: -14.0  I: ...``
#: with ``FTPK: <l> <r> dBFS`` appended when ``peak=true``. ``M`` is the 400ms momentary
#: loudness; ``FTPK`` is that frame's true peak per channel.
_EBUR128_FRAME_RE = re.compile(
    r"^.*?\bt:\s*(?P<t>-?[0-9.]+)\s.*?\bM:\s*(?P<m>-?[0-9.]+|-?inf)\b"
    r"(?:.*?\bFTPK:\s*(?P<ftpk>(?:-?[0-9.]+|-?inf)(?:\s+(?:-?[0-9.]+|-?inf))*)\s*dBFS)?",
    re.MULTILINE,
)


def ebur128_frames(log: str) -> list[dict]:
    """Pure. The per-frame series ebur128 prints while it runs — ``[{"t", "m", "ftpk"}, ...]``
    with ``m`` the momentary loudness (LUFS) and ``ftpk`` the frame's true peak (dBFS, max over
    channels). ``-inf`` becomes ``None``. This is the same pass the integrated figure comes from,
    read at 100ms rather than once — which is what lets V10 tell a soundtrack that has EVENTS from
    a floor that merely has level."""
    frames: list[dict] = []
    for m in _EBUR128_FRAME_RE.finditer(log):
        mom = m.group("m")
        ftpk_raw = m.group("ftpk")
        peaks = [float(x) for x in (ftpk_raw or "").split() if "inf" not in x]
        frames.append(
            {
                "t": float(m.group("t")),
                "m": None if "inf" in mom else float(mom),
                "ftpk": max(peaks) if peaks else None,
            }
        )
    return frames


def momentary_dynamics(frames: list[dict]) -> dict | None:
    """Pure. How much the momentary loudness MOVES, from the 100ms series — the two numbers
    V10's floor-only check reads.

    ``loudness_abruptness_lu`` is the mean absolute SECOND difference of momentary loudness
    (LU per frame², each frame saturated at
    :data:`thresholds.AUDIO_FLOOR_ONLY_EVENT_SATURATION_LU`) and ``loudness_event_fraction`` is
    the share of frames whose second difference exceeds
    :data:`thresholds.AUDIO_FLOOR_ONLY_EVENT_STEP_LU`. Second, not first, difference on
    purpose: a fade is a RAMP and scores ~0 here, while a word, a tick or a chime is a STEP and
    scores high — so a floor that fades in and out still reads as a floor, and a soundtrack
    reads as events. Frames before 0.4s are dropped (the 400ms window is not yet full and
    reports -120) and the series is clipped at ebur128's -70 LUFS "nothing" floor. The
    saturation is what keeps the MEAN honest: a hard gap into digital silence is a ~55 LU step,
    and two such gaps in a 30s floor would otherwise lift the mean past any ceiling a real
    soundtrack clears — an event is an event, not its magnitude. ``None`` when there are too
    few frames to difference."""
    ms = [max(f["m"] if f["m"] is not None else -70.0, -70.0) for f in frames if f["t"] >= 0.4]
    if len(ms) < 3:
        return None
    cap = thresholds.AUDIO_FLOOR_ONLY_EVENT_SATURATION_LU
    d2 = [min(abs(c - 2 * b + a), cap) for a, b, c in zip(ms, ms[1:], ms[2:], strict=False)]
    step = thresholds.AUDIO_FLOOR_ONLY_EVENT_STEP_LU
    return {
        "loudness_abruptness_lu": round(sum(d2) / len(d2), 3),
        "loudness_event_fraction": round(sum(1 for x in d2 if x > step) / len(d2), 4),
        "momentary_frames": len(ms),
    }


def parse_audio_log(log: str, *, duration_s: float | None = None) -> dict:
    """Pure. Everything :func:`measure_audio` derives from the pass's log."""
    runs: list[dict] = []
    pending_start: float | None = None
    for m in re.finditer(
        r"silence_start:\s*(-?[0-9.]+)|silence_end:\s*(-?[0-9.]+)\s*\|\s*"
        r"silence_duration:\s*([0-9.]+)",
        log,
    ):
        if m.group(1) is not None:
            pending_start = float(m.group(1))
        elif m.group(2) is not None and m.group(3) is not None:
            end, dur = float(m.group(2)), float(m.group(3))
            start = pending_start if pending_start is not None else max(0.0, end - dur)
            runs.append({"start": round(start, 3), "end": round(end, 3), "duration": round(dur, 3)})
            pending_start = None
    # A run still open at EOF never gets a silence_end line.
    if pending_start is not None and duration_s:
        tail = duration_s - pending_start
        if tail >= SILENCE_MIN_RUN_S:
            runs.append(
                {
                    "start": round(pending_start, 3),
                    "end": round(duration_s, 3),
                    "duration": round(tail, 3),
                }
            )

    integrated = _last_float(log, r"^\s*I:\s*(-?[0-9.]+)\s*LUFS")
    true_peak = _last_float(log, r"^\s*Peak:\s*(-?[0-9.]+)\s*dBFS")
    # Same ebur128 summary block. "LRA:" is anchored so the "LRA low:"/"LRA high:" lines that
    # follow it cannot match. Both numbers feed V10's floor-only check: a soundtrack has EVENTS,
    # and events show up as loudness that moves (LRA) and peaks that stand above the average
    # (crest). A synthesized noise floor has neither, however loud it is normalised.
    lra = _last_float(log, r"^\s*LRA:\s*(-?[0-9.]+)\s*LU\b")
    crest = (
        round(true_peak - integrated, 1)
        if true_peak is not None and integrated is not None
        else None
    )

    silent_total = sum(r["duration"] for r in runs)
    fraction = (silent_total / duration_s) if duration_s else None

    return {
        "silent_runs": runs,
        "silent_total_s": round(silent_total, 3),
        "silent_fraction": (round(fraction, 4) if fraction is not None else None),
        "integrated_lufs": integrated,
        "true_peak_dbfs": true_peak,
        "loudness_range_lu": lra,
        "crest_db": crest,
        **(momentary_dynamics(ebur128_frames(log)) or {}),
        # -70 LUFS is ffmpeg's "effectively nothing" floor for integrated loudness.
        "has_voice": integrated is not None and integrated > -70.0,
    }


#: How many caption screens V11 samples. A 64-screen asset does not need 64 ffmpeg seeks to learn
#: that its caption style has no plate — the defect is a property of the style, not of one screen.
#: Reported in the finding as "N of M sampled" so the cap is never silent.
CONTRAST_SAMPLE_LIMIT = 12


def measure_caption_contrast(
    path: Path, manifest: dict, *, limit: int = CONTRAST_SAMPLE_LIMIT
) -> list[dict] | None:
    """Impure. Seek to each sampled caption's midpoint, crop its own box, and ask
    :func:`gtm_core.captions.contrast_against_backdrop` what the contrast ratio there is.

    The split is the module boundary: ffmpeg orchestration is this module's job (it already runs
    two other measurement passes), pixel work is captions.py's (see
    tests/media/test_captions_module_boundary.py). Returns ``None`` if ffmpeg or Pillow is
    unavailable, or the manifest carries no usable screens — the caller degrades rather than
    reporting a clean asset it could not measure."""
    import shutil

    try:
        from ..captions import contrast_against_backdrop
    except ImportError:  # Pillow absent — normalize/cut/grade/encode still work without it
        return None

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        return None

    screens = [s for s in (manifest.get("screens") or []) if s.get("box")]
    if not screens:
        return None
    step = max(1, len(screens) // limit)
    sampled = screens[::step][:limit]

    out: list[dict] = []
    for screen in sampled:
        box = screen["box"]
        w, h = int(box.get("w", 0)), int(box.get("h", 0))
        if w <= 0 or h <= 0:
            continue
        start, end = float(screen.get("start_s", 0.0)), float(screen.get("end_s", 0.0))
        at = start + (end - start) / 2 if end > start else start
        try:
            proc = subprocess.run(  # nosec B603 — arg list via shutil.which, never shell=True
                [
                    ffmpeg_bin,
                    "-v",
                    "error",
                    "-ss",
                    f"{at:.3f}",
                    "-i",
                    str(path),
                    "-vf",
                    f"crop={w}:{h}:{int(box.get('x', 0))}:{int(box.get('y', 0))}",
                    "-frames:v",
                    "1",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "png",
                    "-",
                ],
                capture_output=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode != 0 or not proc.stdout:
            continue
        # The screen's own glyph colour, never a white default: with dark type derived from a
        # dark-canvas kit, measuring white-vs-backdrop reported near-black-on-dark as a pass.
        glyph = screen.get("glyph_rgb")
        kwargs = {"glyph_rgb": tuple(int(c) for c in glyph)} if glyph else {}
        measured = contrast_against_backdrop(proc.stdout, **kwargs)
        if measured is not None:
            out.append({"index": screen.get("index"), "at_s": round(at, 3), **measured})
    return out or None


def _last_float(text: str, pattern: str) -> float | None:
    """Last match of a multiline float pattern — ebur128 prints a running summary, then a final
    one; the final block is the asset-level figure and the running ones must not win."""

    found = re.findall(pattern, text, re.MULTILINE)
    return float(found[-1]) if found else None


# ── Transitions (V12) ─────────────────────────────────────────────────────────────────────────


def classify_boundaries(
    diffs: list[tuple[float, float]],
    *,
    min_blend_frames: int,
    max_gap_frames: int = 1,
) -> list[dict]:
    """Split inter-frame deltas into CUTS (discarded here) and TRANSITIONS (returned).

    Pure, so the classifier can be tested on hand-built arrays without ffmpeg — which matters
    because the interesting cases are the boundary ones and rendering a clip per case is slow.

    The shape distinction is the whole rule. A **cut** is one frame of very high delta with quiet
    frames on both sides: the picture is entirely different for exactly one frame-pair. A
    **transition** is a RUN of elevated delta — the two images are being mixed, so every frame
    across the blend differs moderately from the one before it. Counting them together, which is
    what a scene-change list does, makes a dissolve-heavy edit and a cut-heavy one look identical
    while they feel nothing alike.

    The elevation threshold is derived from the asset rather than fixed: a high-contrast graphic
    piece and a soft-focus interview have different resting deltas, and a fixed floor would read
    the first as all transitions and the second as none. Median plus a multiple of the median
    absolute deviation is the robust version of "unusually large for THIS film".
    """
    if len(diffs) < min_blend_frames + 2:
        return []

    values = sorted(v for _, v in diffs)
    n = len(values)
    median = values[n // 2]
    deviations = sorted(abs(v - median) for _, v in diffs)
    mad = deviations[n // 2]
    # The floor stops a near-static clip (median 0, MAD 0) reading its own rounding as motion. It
    # is two luma levels, because that is where "it changed" separates from "it quantised".
    threshold = max(median + 4.0 * mad, thresholds.TRANSITION_MIN_FRAME_DELTA)

    # A run may carry up to `max_gap_frames` sub-threshold frames without ending: an 8-bit
    # dissolve rounds two consecutive outputs to the same value every few frames, and a
    # zero-tolerance detector reports one dissolve as three.
    # `run_last` is always set whenever `run_start` is, so a run closes at `run_last`, full stop.
    runs: list[dict] = []
    run_start = run_last = -1
    gap = 0

    def _close() -> None:
        nonlocal run_start
        if run_start >= 0 and run_last - run_start + 1 >= min_blend_frames:
            runs.append(
                {
                    "start": diffs[run_start][0],
                    "end": diffs[run_last][0],
                    "frames": run_last - run_start + 1,
                }
            )
        run_start = -1

    for i, (_t, value) in enumerate(diffs):
        if value > threshold:
            if run_start < 0:
                run_start = i
            run_last = i
            gap = 0
        elif run_start >= 0:
            gap += 1
            if gap > max_gap_frames:
                _close()
    _close()
    return runs


def measure_transitions(
    path: Path, *, deltas: list[tuple[float, float]] | None = None
) -> list[dict] | None:
    """Impure unless ``deltas`` is supplied. The multi-frame blends in ``path``.

    ``None`` means it could not be measured — no ffmpeg, a failed pass, or too few frames — and
    the caller must skip V12 rather than report zero transitions, which would be a clean verdict
    nothing earned. Pass ``deltas`` from :func:`frame_deltas` to share one decode with V9: the
    first version ran its own byte-identical tblend pass, a second full decode of the asset for
    data the motion pass had already produced and thrown away.
    """
    from .thresholds import TRANSITION_MAX_GAP_FRAMES, TRANSITION_MIN_BLEND_FRAMES

    diffs = deltas if deltas is not None else frame_deltas(path)
    if not diffs:
        return None
    return classify_boundaries(
        diffs,
        min_blend_frames=TRANSITION_MIN_BLEND_FRAMES,
        max_gap_frames=TRANSITION_MAX_GAP_FRAMES,
    )
