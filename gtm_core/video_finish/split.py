from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .constants import DEFAULT_CRF, DEFAULT_GOP, DEFAULT_PRESET, DEFAULT_THREADS
from .errors import FfmpegUnavailable
from .ffmpeg import _probe_duration, _run_ffmpeg

#: How far past the probed source duration a cut's ``end_s`` may sit before :func:`split` refuses
#: it. One frame at 24fps is ~0.042s, and a container's reported duration and its last video
#: frame's presentation time routinely disagree by about that much — so a cut that asks for the
#: whole take must not trip on rounding. Anything past this is a real overrun, not an artefact.
SPLIT_END_TOLERANCE_S = 0.05


@dataclass(frozen=True)
class TakeCut:
    """One beat's MEASURED in/out inside a continuous presenter take.

    ``shot_id`` names the output file, so it is caller data reaching a filesystem path and
    :func:`split` refuses anything that is not a bare segment.
    """

    shot_id: str
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass(frozen=True)
class SplitResult:
    """What :func:`split` actually cut — recorded rather than inferred, because the take's real
    duration is the fact the shot list's ASKED durations get mistaken for."""

    out_paths: tuple[Path, ...]
    source_duration_s: float
    cuts: tuple[TakeCut, ...]


def split(
    src: Path,
    cuts: list[TakeCut],
    *,
    out_dir: Path,
    workdir: Path,
) -> SplitResult:
    """Cut ONE continuous take into its per-beat files — the inverse of :func:`stitch`.

    HISTORY, because this verb's reason for existing has moved twice. It was added 2026-08-29
    when `video-avatar` Step 3 rendered the presenter's whole script as one continuous take on a
    per-job reading of HeyGen billing — that left the presenter footage as ONE file while
    `video-render`'s inserts stayed separate, :func:`stitch` only joins, and ``Bash(ffmpeg:*)``
    is denied, so "cut it locally" named an operation with no reachable implementation. On
    2026-09-07 the per-job reading was retired: HeyGen bills per SECOND (`gtm_core.heygen_cost`,
    four balance-delta measurements), so per-beat costs the same as one take and `video-avatar`
    renders per beat again. **No skill body invokes this verb any more.** It is kept as an
    operator utility — footage that arrives as one file for any other reason still needs a
    frame-accurate, non-stream-copy cut behind the only sanctioned ffmpeg door — but it no longer
    has a cost rationale and should not be cited as one.

    **Every cut is re-encoded, never stream-copied.** ``-c copy`` can only cut on a keyframe, so it
    silently snaps each in-point back to the nearest preceding one — on a lip-synced presenter beat
    that lands the cut mid-word and slides the whole beat against its own audio. The re-encode buys
    frame accuracy, which is the entire point of cutting to measured out-points.

    Refusals, most of them the same defect seen from different angles:

    * ``end_s`` past the take's real duration → :class:`ValueError`. This is exactly what an ASKED
      timing copied from the shot list looks like once the delivered take ran shorter, which is
      the desync the retired "one clip per presenter shot" guardrail named and Step 3 now owns.
    * ``end_s <= start_s``, a negative ``start_s``, a duplicate ``shot_id``, or a ``shot_id`` that
      is not a bare path segment.

    Overlapping cuts are ALLOWED: :func:`stitch`'s ``crossfade_s`` path needs overlapping
    tails/heads, so refusing them here would fight a sibling verb. Gaps are allowed too — dropping
    a fluffed line is a legitimate edit, not an error.

    Cuts land on the nearest frame boundary, so two adjacent cuts sharing a timecode may repeat at
    most one frame at the seam (~33ms at 30fps). That is below a hard cut's visible threshold and
    is deliberately not corrected here.
    """
    if not cuts:
        raise ValueError("split() needs at least one cut")

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")

    seen: set[str] = set()
    for cut in cuts:
        if (
            not cut.shot_id
            or "/" in cut.shot_id
            or "\\" in cut.shot_id
            or "\x00" in cut.shot_id
            or cut.shot_id in (".", "..")
        ):
            raise ValueError(
                f"unsafe shot_id {cut.shot_id!r} — must be a bare filename segment (no separator, "
                "no '..'): it names a file inside the output directory"
            )
        if cut.shot_id in seen:
            raise ValueError(
                f"duplicate shot_id {cut.shot_id!r} — two cuts would write the same file, and the "
                "second would silently overwrite the first"
            )
        seen.add(cut.shot_id)
        if cut.start_s < 0:
            raise ValueError(
                f"cut {cut.shot_id!r} starts at {cut.start_s}s, before the beginning of the take"
            )
        if cut.end_s <= cut.start_s:
            raise ValueError(
                f"cut {cut.shot_id!r} ends at {cut.end_s}s, at or before its {cut.start_s}s start"
            )

    source_s = _probe_duration(src)
    for cut in cuts:
        if cut.end_s > source_s + SPLIT_END_TOLERANCE_S:
            raise ValueError(
                f"cut {cut.shot_id!r} ends at {cut.end_s:.3f}s but the take is only "
                f"{source_s:.3f}s long. This is what an ASKED duration from the shot list looks "
                "like when the delivered take ran shorter — measure the out-points off the "
                "rendered take rather than carrying the shot list's numbers forward as if they "
                "described the file."
            )

    out_dir.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)

    out_paths: list[Path] = []
    for cut in cuts:
        out_path = out_dir / f"{cut.shot_id}.mp4"
        part_path = workdir / f"{out_path.name}.part"
        args = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(src),
            # Output-side seek (`-ss` AFTER `-i`): decode from the start and discard, which is
            # frame-accurate at any in-point. `-t` is a DURATION rather than a timestamp, so
            # there is no ambiguity about which timeline it is measured against.
            "-ss",
            f"{cut.start_s:.6f}",
            "-t",
            f"{cut.duration_s:.6f}",
            "-c:v",
            "libx264",
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
        out_paths.append(out_path)

    return SplitResult(out_paths=tuple(out_paths), source_duration_s=source_s, cuts=tuple(cuts))
