from __future__ import annotations

import json
import re
import shutil
import subprocess  # nosec B404 — ffmpeg/ffprobe orchestration, arg lists only, never shell=True
from pathlib import Path

from .errors import FfmpegUnavailable, SfxError


def _run_ffmpeg(args: list[str]) -> None:
    subprocess.run(args, check=True)  # nosec B603 — arg list, never shell=True


def _run_ffmpeg_capture(args: list[str], *, timeout: float = 120.0) -> str:
    """Sibling of :func:`_run_ffmpeg` for a MEASURING pass, where the result of the run is TEXT
    rather than a file. ffmpeg prints every filter's diagnostic to stderr at ``-v info``
    (``volumedetect``'s ``max_volume:``, ``ebur128``'s summary), so a measurement routed through
    :func:`_run_ffmpeg` throws away the only thing it was run for.

    ``check=False`` is the one place this differs from its sibling, deliberately: a run that
    exits non-zero having already printed a parseable measurement is still a measurement, and
    the caller — not ``subprocess`` — decides whether the number is usable. The parsers raise
    naming the missing line when it is not there.

    Returns stderr concatenated with stdout: which of the two carries filter diagnostics has
    varied across builds, and the parsers anchor on the label, never on the stream.
    """
    out = subprocess.run(  # nosec B603 — arg list, never shell=True
        args, check=False, capture_output=True, text=True, timeout=timeout
    )
    return (out.stderr or "") + (out.stdout or "")


def _probe_fps(path: Path) -> float:
    """Real output frame rate as a float (e.g. '24/1' -> 24.0, '24000/1001' -> 23.976...).

    PREFERS ``avg_frame_rate``, FALLS BACK TO ``r_frame_rate``, AND THAT ORDER IS LOAD-BEARING.
    ``r_frame_rate`` is not the file's frame rate — it is the lowest rate that can express every
    frame duration in the stream, so a container written on a fine timebase reports a nominal
    multiple of the real rate. Measured 2026-09-03: a concat-demuxer STREAM COPY of two 25fps
    shots (203 frames, 8.13s) came out on a 1/12800 timebase reporting ``r_frame_rate = 100/1``
    while ``avg_frame_rate`` correctly said 24.97 and the frame count was untouched.

    That was harmless for as long as nothing acted on the reading. It stopped being harmless the
    same day, when :func:`_normalize_shot` gained ``target_fps``: :func:`stitch` reads these rates,
    finds "100fps" segments beside 25fps ones, pins ``-r 100`` on the lot, and a 3:47 film is
    genuinely re-encoded to 22,715 frames instead of 5,690. Worse, it passes ``video_lint``'s V1
    check MORE cleanly than the correct file, because 100 clears a floor that 25 does not — the
    defect silences the one gate that would have named it.

    Needed by :func:`predictor_trim` to compute a safety margin — see that function's docstring
    for why a plain ``-t`` target isn't reliable on its own.
    """
    ffprobe_bin = shutil.which("ffprobe")
    if ffprobe_bin is None:
        raise FfmpegUnavailable("ffprobe is not on PATH")
    out = subprocess.run(  # nosec B603 — arg list resolved via shutil.which, never shell=True
        [
            ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate,r_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    stream = json.loads(out.stdout)["streams"][0]
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = str(stream.get(key) or "")
        num, _, den = raw.partition("/")
        try:
            rate = float(num) / float(den or 1)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        # `avg_frame_rate` is 0/0 on a stream with no frames and on some image inputs; that is
        # the case r_frame_rate exists to cover, so a zero is a MISS rather than an answer.
        if rate > 0:
            return rate
    raise FfmpegUnavailable(f"{path} reports no usable frame rate ({stream!r})")


def _probe_dims(path: Path) -> tuple[int, int]:
    ffprobe_bin = shutil.which("ffprobe")
    if ffprobe_bin is None:
        raise FfmpegUnavailable("ffprobe is not on PATH")
    out = subprocess.run(  # nosec B603 — arg list resolved via shutil.which, never shell=True
        [
            ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    stream = json.loads(out.stdout)["streams"][0]
    return int(stream["width"]), int(stream["height"])


def _probe_duration(path: Path, *, stream_selector: str | None = "v:0") -> float:
    """Return a media duration in seconds.

    ``stream_selector`` picks which stream's own duration is preferred — ``"v:0"`` (the default,
    used by the crossfade math so it fails closed when a transition outruns its shortest segment)
    or ``None`` to skip stream selection entirely, which is what an audio-only VO track needs.
    """
    ffprobe_bin = shutil.which("ffprobe")
    if ffprobe_bin is None:
        raise FfmpegUnavailable("ffprobe is not on PATH")
    select_args = ["-select_streams", stream_selector] if stream_selector else []
    out = subprocess.run(  # nosec B603 — arg list resolved via shutil.which, never shell=True
        [
            ffprobe_bin,
            "-v",
            "error",
            *select_args,
            "-show_entries",
            "stream=duration:format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(out.stdout)
    # Stream duration first, container duration as the fallback — this preserves the historical
    # behavior exactly. Two latent bugs were fixed here on 2026-08-19 while wiring the mux path,
    # both of which only ever bit an audio-only input:
    #   1. `-show_entries stream=duration,format=duration` never requested format.duration at all —
    #      ffprobe separates SECTIONS with ':', not ','. The format fallback was dead code.
    #   2. `payload["streams"][0]` on a file with no matching stream raised IndexError.
    # A video file always has a v:0 duration, so neither surfaced until a .mp3 was probed.
    streams = payload.get("streams") or [{}]
    raw = streams[0].get("duration") or payload.get("format", {}).get("duration")
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise FfmpegUnavailable(f"could not probe duration from {path}: {raw!r}") from exc


_MAX_VOLUME_RE = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")
_N_SAMPLES_RE = re.compile(r"n_samples:\s*(\d+)")


def _parse_max_volume(log: str) -> float:
    """``volumedetect`` prints ``max_volume: -6.0 dB`` at ``-v info``.

    A window of pure digital silence prints no ``max_volume`` line at all on some builds, so an
    absent line WITH an ``n_samples`` line is -inf — a legitimate measurement, and it is the
    entire left half of most cue files. An absent line with no ``n_samples`` either means the
    filter never ran, which is an error worth naming.
    """
    m = _MAX_VOLUME_RE.search(log)
    if m:
        return float(m.group(1))
    if _N_SAMPLES_RE.search(log):
        return float("-inf")
    raise SfxError(f"volumedetect produced no measurement; ffmpeg said:\n{log[-800:]}")


def _measure_window_dbfs(
    path: Path, *, start_s: float, duration_s: float, stream: str = "a:0"
) -> float:
    """Peak level of one time window of one audio stream, in dBFS.

    The window is cut with ``atrim`` INSIDE the filter chain, not with ``-ss``/``-t``. Both other
    options are wrong here for different reasons: input seeking (``-ss`` before ``-i``) is
    keyframe-quantised on a compressed file, so the window boundaries become approximate — and
    approximate window boundaries are precisely the class of error this feature exists to fix —
    while output seeking (``-ss`` after ``-i``) does not reach the filter graph at all on
    ffmpeg 8.x: measured 2026-08-31, every window of a decaying click reported the same
    ``max_volume``, i.e. the whole file's. ``atrim`` is unambiguous, format-independent, and is
    what :func:`_null_residual_dbfs` already uses, so both measurements cut their windows the
    same way.
    """
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")
    a = max(0.0, start_s)
    b = a + max(0.001, duration_s)
    log = _run_ffmpeg_capture(
        [
            ffmpeg_bin,
            "-hide_banner",
            "-nostats",
            "-v",
            "info",
            "-i",
            str(path),
            "-map",
            stream,
            "-af",
            f"atrim=start={a:.4f}:end={b:.4f},asetpts=PTS-STARTPTS,volumedetect",
            "-f",
            "null",
            "-",
        ]
    )
    return _parse_max_volume(log)


def _probe_stream_kinds(path: Path) -> tuple[bool, bool]:
    """(has_video, has_audio). Sibling of :func:`_probe_dims`, same ffprobe discipline."""
    ffprobe_bin = shutil.which("ffprobe")
    if ffprobe_bin is None:
        raise FfmpegUnavailable("ffprobe is not on PATH")
    out = subprocess.run(  # nosec B603 — arg list resolved via shutil.which, never shell=True
        [
            ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    kinds = {s.get("codec_type") for s in json.loads(out.stdout).get("streams", [])}
    return "video" in kinds, "audio" in kinds
