from __future__ import annotations

import os
import shutil
from pathlib import Path

from .constants import DEFAULT_CRF
from .errors import FfmpegUnavailable
from .ffmpeg import _run_ffmpeg


def safe_area_or_raise(ratio: str):
    """The :data:`gtm_core.video_lint.SAFE_AREAS` entry for ``ratio``, or the one refusal.

    ``SAFE_AREAS`` is the single home for what a ratio means; every producer of a frame at a
    ratio (the delivered master, the animatic's stills) resolves it here, so the vocabulary and
    the refusal message cannot drift between them.
    """
    from gtm_core.video_lint import SAFE_AREAS

    area = SAFE_AREAS.get(ratio)
    if area is None:
        raise ValueError(
            f"unknown ratio {ratio!r} — known: {', '.join(sorted(SAFE_AREAS))}. "
            "Add it to video_lint.SAFE_AREAS (the one home for what a ratio means) first."
        )
    return area


def letterbox_vf(area, *, background: str | None = None) -> str:
    """The ONE scale-then-pad chain that fits a frame inside ``area`` without stretching.

    Shared by the delivered master (:func:`pad_to_ratio`) and the animatic's stills
    (:mod:`gtm_core.video_finish.stills`) on purpose: the animatic exists to preview the master's
    framing, so if the two ever pad differently the preview lies about the one thing it is for.
    ``background`` is ffmpeg's ``pad`` colour; ``None`` leaves the default.
    """
    colour = f":color={background}" if background else ""
    return (
        f"scale={area.width}:{area.height}:force_original_aspect_ratio=decrease,"
        f"pad={area.width}:{area.height}:(ow-iw)/2:(oh-ih)/2{colour},"
        "setsar=1"
    )


def pad_to_ratio(
    src: Path,
    *,
    ratio: str,
    out_path: Path,
    workdir: Path,
    background: str = "black",
    video_bitrate: str | None = None,
    crop_top_px: int = 0,
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

    ``crop_top_px`` is a DIFFERENT operation from the pad this function is named for — it removes
    OS chrome (a status bar's clock, battery, carrier/hotspot glyphs) from the top of a raw device
    screen recording before the letterbox math runs, rather than reframing a finished master to a
    new platform ratio. It exists here rather than as its own function because both are one
    ``-vf`` chain and one part-file/``os.replace`` write, and duplicating that plumbing for a strip
    crop would be the second copy CLAUDE.md's "surgical changes" guidance warns against. Default 0
    leaves this function's original behaviour untouched.
    """
    area = safe_area_or_raise(ratio)
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")
    if crop_top_px < 0:
        raise ValueError(f"crop_top_px must be >= 0, got {crop_top_px}")
    workdir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = out_path.with_suffix(out_path.suffix + ".part")

    vf_chain = letterbox_vf(area, background=background)
    if crop_top_px:
        vf_chain = f"crop=iw:ih-{crop_top_px}:0:{crop_top_px}," + vf_chain
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
