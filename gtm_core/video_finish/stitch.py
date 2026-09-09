from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..video_lint import SAFE_AREAS
from .constants import DEFAULT_CRF, DEFAULT_GOP, DEFAULT_PRESET, DEFAULT_THREADS
from .errors import FfmpegUnavailable
from .ffmpeg import _probe_duration, _probe_fps, _run_ffmpeg
from .shots import ShotSegment, _normalize_shot


def stitch(
    segments: list[ShotSegment],
    *,
    ratio: str,
    out_path: Path,
    workdir: Path,
    crossfade_s: float = 0.0,
    normalize: bool = False,
    video_bitrate: str | None = None,
) -> Path:
    """Concatenate finished per-shot files into one asset.

    By default this uses the ffmpeg CONCAT DEMUXER with ``-c copy`` — the cheapest path, and
    correct when every segment came from the same locked encode settings. A segment already at the
    target ratio's pixel dimensions and not flagged ``reframed`` is returned untouched, so only
    shots that actually need it pay a re-encode cost.

    ``video_bitrate`` (e.g. ``"8M"``) swaps the constant-quality CRF encode for a bitrate target.
    CRF is the better default — it spends bits where the picture needs them — but it is a QUALITY
    target, so a film that is half flat graphics lands at a low average bitrate and trips
    ``video_lint``'s 6 Mbps floor at 1080p even though nothing looks compressed. Set this on a
    final deliverable when that floor has to be met; leave it unset for intermediates.

    ``normalize=True`` re-encodes EVERY segment to those settings first. Use it when the segments
    came from DIFFERENT encoders — a provider's avatar render beside a locally encoded frame
    sequence. Both may report identical width, height, pix_fmt, profile and time_base and still
    carry different SPS/PPS, and an MP4 can hold only one of those: the stream-copy concat then
    produces a file that plays for a few segments and is undecodable after — with no error at all.
    Observed 2026-08-29, where a 2:42 master came out 9:45 at 9.47fps and only failed when
    something tried to decode it.

    This exists so that case has an honest lever. It was reachable before only by setting
    ``reframed=True`` on segments that had not been reframed, which fixes the encode by writing a
    false statement into the manifest.

    When ``crossfade_s > 0`` the join is re-encoded via ``filter_complex`` using ``xfade`` for
    video and ``acrossfade`` for audio. This is intentionally more expensive: crossfades cannot be
    done with stream copy. The requested duration is validated against the shortest normalized
    segment — if a segment is too short to hold the transition, the function raises rather than
    silently producing a malformed output.
    """
    if not segments:
        raise ValueError("stitch() needs at least one segment")
    if ratio not in SAFE_AREAS:
        raise ValueError(f"unknown ratio {ratio!r} — expected one of {sorted(SAFE_AREAS)}")
    area = SAFE_AREAS[ratio]
    workdir.mkdir(parents=True, exist_ok=True)

    # Frame rate is part of "uniform encode settings". The concat demuxer stream-copies, so it
    # cannot reconcile two rates: it emits a non-monotonic-DTS storm and a file whose duration is
    # wrong. Mixed rates are the NORM for this pipeline (a 24fps provider render beside a 30fps
    # local frame sequence), so detect it rather than trusting the caller to notice.
    rates = [_probe_fps(Path(seg.path)) for seg in segments]
    mixed_fps = len({round(r, 3) for r in rates}) > 1
    target_fps = max(rates) if mixed_fps else None
    if mixed_fps and not normalize:
        raise ValueError(
            f"segments carry {len({round(r, 3) for r in rates})} different frame rates "
            f"({', '.join(f'{r:g}' for r in sorted(set(rates)))}fps) — the concat demuxer stream-"
            f"copies and cannot reconcile them, so this would emit a file with the wrong duration "
            f"and no error. Pass normalize=True to re-encode every segment to {max(rates):g}fps."
        )

    normalized = [
        _normalize_shot(
            Path(seg.path),
            target_w=area.width,
            target_h=area.height,
            workdir=workdir / "normalized",
            force_reencode=seg.reframed or normalize,
            video_bitrate=video_bitrate,
            target_fps=target_fps,
        )
        for seg in segments
    ]

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = out_path.with_suffix(out_path.suffix + ".part")

    if crossfade_s <= 0.0 or len(normalized) == 1:
        # Hard-cut path: concat demuxer + stream copy.
        list_path = workdir / "concat_list.txt"
        list_path.write_text(
            "\n".join(f"file '{p.resolve().as_posix()}'" for p in normalized) + "\n"
        )
        args = [
            ffmpeg_bin,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            "-f",
            "mp4",
            str(part_path),
        ]
        _run_ffmpeg(args)
        os.replace(part_path, out_path)
        return out_path

    # Crossfade path: filter_complex re-encode.
    durations = [_probe_duration(p) for p in normalized]
    min_segment = min(durations)
    min_supported = crossfade_s * 2
    if min_segment < min_supported:
        raise ValueError(
            f"requested crossfade_s={crossfade_s}s requires each segment to be at least "
            f"{min_supported}s, but the shortest normalized segment is {min_segment:.3f}s"
        )

    inputs: list[str] = []
    for p in normalized:
        inputs.extend(["-i", str(p)])

    # Video: chain xfade filters. Transition k starts at sum(durations[0..k]) - (k+1)*crossfade_s.
    v_parts: list[str] = []
    v_label = "0:v"
    for i in range(1, len(normalized)):
        offset = sum(durations[:i]) - i * crossfade_s
        out_label = f"vf{i}"
        if i == len(normalized) - 1:
            out_label = "vout"
        v_parts.append(
            f"[{v_label}][{i}:v]xfade=transition=fade:duration={crossfade_s}:"
            f"offset={offset:.6f}[{out_label}]"
        )
        v_label = out_label

    # Audio: chain acrossfade filters. Each transition overlaps the last `crossfade_s` seconds
    # of the accumulated output with the start of the next input. acrossfade has no offset param:
    # it always crossfades over the overlapping tail/head of its two inputs.
    a_parts: list[str] = []
    a_label = "0:a"
    for i in range(1, len(normalized)):
        out_label = f"af{i}"
        if i == len(normalized) - 1:
            out_label = "aout"
        a_parts.append(f"[{a_label}][{i}:a]acrossfade=d={crossfade_s}[{out_label}]")
        a_label = out_label

    filtergraph = ";".join(v_parts + a_parts)

    args = [
        ffmpeg_bin,
        "-y",
        *inputs,
        "-filter_complex",
        filtergraph,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        # Pinned, not inherited. xfade promotes its output to yuv444p, which libx264 will happily
        # encode — and the resulting file then CANNOT be stream-copy concatenated with any
        # yuv420p segment, so a nested stitch (hard cuts inside groups, dissolves between them)
        # silently produced a master with mangled timestamps rather than an error. yuv420p is also
        # what broad playback requires. `frames_to_video` already pins it; this path had drifted.
        "-pix_fmt",
        "yuv420p",
        "-crf",
        str(DEFAULT_CRF),
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
        "-f",
        "mp4",
        str(part_path),
    ]
    _run_ffmpeg(args)
    os.replace(part_path, out_path)
    return out_path
