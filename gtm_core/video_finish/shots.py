from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..shots_lint.transition import transition_errors, transition_in
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


def transitions_from_shots(doc: dict) -> list[tuple[str, float]]:
    """The per-join ``(kind, duration_s)`` list :func:`stitch` takes, read off a shot list.

    Join k is the cut INTO ``shots[k+1]``, so a list of n shots yields n-1 entries: a shot's
    ``production.transition_in`` describes how it is cut into, and a missing one is a hard cut,
    ``("cut", 0.0)``. The segments handed to :func:`stitch` must be the same shots in the same
    order — that positional agreement is the whole contract, which is why this derives the list
    once rather than each caller matching shots to segments its own way.

    A malformed spec raises with the linter's own message (:func:`transition_errors` is the one
    judge of the shape), and so does one on the FIRST shot, which has no join to be cut into.
    """
    shots = doc.get("shots") if isinstance(doc, dict) else None
    if not isinstance(shots, list) or not shots:
        raise ValueError("shot list has no non-empty `shots` array to read transitions from")
    if transition_in(shots[0]) is not None:
        raise ValueError(
            "shots[0].production.transition_in is declared on the first shot, which has no shot "
            "before it to be cut from"
        )
    out: list[tuple[str, float]] = []
    for k, shot in enumerate(shots[1:], 1):
        entry = transition_in(shot) if isinstance(shot, dict) else None
        if entry is None:
            out.append(("cut", 0.0))
            continue
        errors = transition_errors(entry, f"shots[{k}].production.transition_in")
        if errors:
            raise ValueError("; ".join(errors))
        out.append((str(entry["kind"]), float(entry["duration_s"])))
    return out


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
