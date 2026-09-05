from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .constants import (
    AUDIO_CHANNELS,
    AUDIO_SAMPLE_RATE,
    DEFAULT_CRF,
    DEFAULT_GOP,
    DEFAULT_PRESET,
    DEFAULT_THREADS,
)
from .errors import FfmpegUnavailable
from .ffmpeg import _probe_dims, _run_ffmpeg


@dataclass(frozen=True)
class ShotSegment:
    """One finished per-shot file (already muxed video + its own VO) feeding :func:`stitch`.
    ``reframed=True`` is the caller's own signal that this shot's ratio/crop diverged from the
    others and therefore MUST be re-encoded before the join — never inferred silently."""

    path: str
    reframed: bool = False


def _normalize_shot(
    src: Path,
    *,
    target_w: int,
    target_h: int,
    workdir: Path,
    force_reencode: bool,
    video_bitrate: str | None = None,
    target_fps: float | None = None,
) -> Path:
    """Return a shot file guaranteed to match ``target_w``x``target_h`` — and, when it re-encodes,
    guaranteed to carry ``AUDIO_SAMPLE_RATE``/``AUDIO_CHANNELS`` audio, which the concat demuxer
    needs uniform across segments for the same reason it needs uniform dimensions. Re-encodes ONLY
    when
    ``force_reencode`` (the shot was reframed) or the shot's own dims already differ from
    target — everything else passes through untouched. This is what makes "re-encode only where
    reframed" actually cheaper than re-encoding the whole batch: a shot rendered at the correct
    ratio to begin with costs zero extra encode work here.

    ``target_fps`` pins the output frame rate. FRAME RATE IS PART OF "UNIFORM ENCODE SETTINGS" AND
    WAS MISSING HERE UNTIL 2026-09-03: this function unified dimensions and audio but left each
    segment at its own rate, so a caller that asked for normalization still handed the concat
    demuxer a 24fps provider render beside a 30fps local frame sequence. Stream copy cannot
    reconcile two rates — it emits a non-monotonic-DTS storm and a file whose duration is simply
    wrong (a 30.000s nine-shot cut came out 37.500s). That is the exact defect :func:`stitch`'s
    own docstring already described and ``normalize=True`` was added to cure, so the flag was
    curing two thirds of it. Left as ``None`` the rate is untouched, which is the prior
    behaviour."""
    w, h = _probe_dims(src)
    if not force_reencode and (w, h) == (target_w, target_h):
        return src
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")
    workdir.mkdir(parents=True, exist_ok=True)
    dst = workdir / f"norm_{src.stem}.mp4"
    part = dst.with_suffix(dst.suffix + ".part")
    args = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(src),
        "-vf",
        f"scale={target_w}:{target_h}",
        *(["-r", f"{target_fps:g}"] if target_fps else []),
        "-c:v",
        "libx264",
        *(
            ["-b:v", video_bitrate, "-maxrate", video_bitrate, "-bufsize", f"{video_bitrate}"]
            if video_bitrate
            else ["-crf", str(DEFAULT_CRF)]
        ),
        "-preset",
        str(DEFAULT_PRESET),
        "-g",
        str(DEFAULT_GOP),
        "-threads",
        str(DEFAULT_THREADS),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        str(AUDIO_SAMPLE_RATE),
        "-ac",
        str(AUDIO_CHANNELS),
        "-f",
        "mp4",
        str(part),
    ]
    _run_ffmpeg(args)
    os.replace(part, dst)
    return dst
