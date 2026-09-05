from __future__ import annotations

import os
import shutil
from pathlib import Path

from .constants import DEFAULT_CRF
from .errors import FfmpegUnavailable
from .ffmpeg import _run_ffmpeg


def pad_to_ratio(
    src: Path,
    *,
    ratio: str,
    out_path: Path,
    workdir: Path,
    background: str = "black",
    video_bitrate: str | None = None,
) -> Path:
    """Refit a finished master into another platform ratio by SCALING AND PADDING, never cropping.

    WHY PAD RATHER THAN CROP, AND WHY THIS IS NOT JUST A WORSE `reframe`. Reap's tracking
    reframe is the better tool whenever the subject is a face — it follows the speaker and
    fills the frame. It has two limits that make it the wrong tool here: it emits only 9:16
    and 1:1 (checked live 2026-08-29 — there is no 4:5 orientation, which is the ratio the
    feed cut actually wants), and it CROPS. A film whose explanatory spine is full-frame
    graphics cannot be cropped: :mod:`gtm_core.screen_ui` lays a record card or a compare
    track across the whole 16:9 frame and floors its payload type at
    ``_PAYLOAD_MIN_H_FRAC`` of the height, so taking 44% off the width of a 16:9 master to
    reach 4:5 removes labels, not letterboxing. Face-tracking cannot help — there is no face
    in those shots to track.

    So the rule is per-asset, not per-tool: **crop a talking head, pad a designed frame.** This
    function is the pad half, and it exists in this module for the same reason everything else
    here does — the alternative is an ffmpeg invocation outside :func:`_run_ffmpeg`.

    Target pixel dimensions come from :data:`gtm_core.video_lint.SAFE_AREAS`, which is already
    the single source of truth for what each ratio means, so a ratio this repo can LINT is by
    construction a ratio it can also produce. Before this, ``video_lint --ratio 4:5`` gated an
    artifact nothing in the repo could make.

    ``background`` is passed to ffmpeg's ``pad`` colour, so a brand colour can fill the bars
    instead of black; audio is stream-copied and never re-encoded.
    """
    from gtm_core.video_lint import SAFE_AREAS

    area = SAFE_AREAS.get(ratio)
    if area is None:
        raise ValueError(
            f"unknown ratio {ratio!r} — known: {', '.join(sorted(SAFE_AREAS))}. "
            "Add it to video_lint.SAFE_AREAS (the one home for what a ratio means) first."
        )
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")
    workdir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = out_path.with_suffix(out_path.suffix + ".part")

    vf_chain = (
        f"scale={area.width}:{area.height}:force_original_aspect_ratio=decrease,"
        f"pad={area.width}:{area.height}:(ow-iw)/2:(oh-ih)/2:color={background},"
        "setsar=1"
    )
    _run_ffmpeg(
        [
            ffmpeg_bin,
            "-y",
            "-i",
            str(src),
            "-vf",
            vf_chain,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            # A padded frame is mostly flat colour, which CRF spends almost nothing on — the
            # same trap that put a 1080p master at 2.06 Mbps under the 6 Mbps floor. An
            # explicit target is the only thing that clears V1 on this kind of picture.
            *(
                ["-b:v", video_bitrate, "-maxrate", video_bitrate, "-bufsize", video_bitrate]
                if video_bitrate
                else ["-crf", str(DEFAULT_CRF)]
            ),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            str(part_path),
        ]
    )
    os.replace(part_path, out_path)
    return out_path
