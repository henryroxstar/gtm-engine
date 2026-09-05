from __future__ import annotations

import re
import subprocess  # nosec B404 — ffprobe orchestration, arg list only, never shell=True
from pathlib import Path

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


def measure_cuts_and_motion(path: Path) -> tuple[list[float] | None, list[dict] | None]:
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
                "select='gt(scene,0.3)',metadata=print:file=-",
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

    try:
        out2 = subprocess.run(  # nosec B603 — arg list resolved via shutil.which, never shell=True
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
        return (cuts or None), None

    samples: list[tuple[float, float]] = []
    pending_t: float | None = None
    for line in (out2.stdout or "").splitlines():
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            pending_t = float(m.group(1))
            continue
        m = re.search(r"lavfi\.signalstats\.YAVG=([0-9.]+)", line)
        if m and pending_t is not None:
            samples.append((pending_t, float(m.group(1)) / 255.0))
            pending_t = None
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
    log = (out.stderr or "") + (out.stdout or "")

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

    silent_total = sum(r["duration"] for r in runs)
    fraction = (silent_total / duration_s) if duration_s else None

    return {
        "silent_runs": runs,
        "silent_total_s": round(silent_total, 3),
        "silent_fraction": (round(fraction, 4) if fraction is not None else None),
        "integrated_lufs": integrated,
        "true_peak_dbfs": true_peak,
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
        measured = contrast_against_backdrop(proc.stdout)
        if measured is not None:
            out.append({"index": screen.get("index"), "at_s": round(at, 3), **measured})
    return out or None


def _last_float(text: str, pattern: str) -> float | None:
    """Last match of a multiline float pattern — ebur128 prints a running summary, then a final
    one; the final block is the asset-level figure and the running ones must not win."""

    found = re.findall(pattern, text, re.MULTILINE)
    return float(found[-1]) if found else None
