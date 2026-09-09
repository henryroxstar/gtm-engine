"""Turn one still into a video segment of an exact length — the animatic's building block.

Lives inside :mod:`gtm_core.video_finish` because ``tests/contracts/test_ffmpeg_module_boundary.py``
allows ffmpeg only here, in :mod:`gtm_core.video_lint`, and in ``cover_frame.py``. That boundary is
the reason the animatic is not three lines of shell.

The segments this produces are deliberately compatible with what :func:`frames_to_video` makes —
same encoder, same pixel format, same silent stereo track — so a stitch can join a still-derived
segment to a rendered one without a normalize pass discovering the mismatch mid-concat.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .errors import FfmpegUnavailable
from .ffmpeg import _run_ffmpeg

__all__ = ["ANIMATIC_FPS", "still_to_segment"]

#: The animatic's frame rate. Low on purpose — nothing here is delivered, and every extra frame is
#: encode time on a preview whose only job is to be watched once. It still has to be a rate a
#: normal player handles without judder, which rules out the very low numbers.
ANIMATIC_FPS = 24


def still_to_segment(
    still: Path,
    *,
    len_s: float,
    ratio: str,
    out_path: Path,
    fps: int = ANIMATIC_FPS,
) -> Path:
    """Hold ``still`` on screen for exactly ``len_s`` seconds, as a segment ready to stitch.

    The still is letterboxed into the ratio's frame rather than stretched, and the frame comes
    from :data:`gtm_core.video_lint.SAFE_AREAS` — the same single source of truth every other
    ratio-aware function here reads, so an animatic can only be built at a ratio this repo can
    also lint. Padding matters more than it looks: storyboard stills arrive at whatever dimensions
    the provider coerced them to, and a stitch of mixed sizes either fails or silently rescales.

    A silent stereo track is attached for the same reason :func:`frames_to_video` attaches one —
    ``stitch`` assumes every segment has an audio stream, and a video-only segment desyncs a
    concat that expects one.
    """
    from .ratio import letterbox_vf, safe_area_or_raise

    if len_s <= 0:
        raise ValueError(f"len_s must be > 0, got {len_s}")
    if fps <= 0:
        raise ValueError(f"fps must be > 0, got {fps}")
    area = safe_area_or_raise(ratio)
    if not still.is_file():
        raise FileNotFoundError(f"still not found: {still}")

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = out_path.with_suffix(out_path.suffix + ".part")
    # The master's own letterbox chain plus the animatic's frame rate — the same chain by
    # construction, so the preview cannot pad differently from the thing it previews.
    vf = f"{letterbox_vf(area)},fps={fps}"
    _run_ffmpeg(
        [
            ffmpeg_bin,
            "-y",
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-t",
            f"{len_s:.3f}",
            "-i",
            str(still),
            "-f",
            "lavfi",
            "-t",
            f"{len_s:.3f}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            # ffmpeg cannot infer a muxer from a `.part` suffix — the same trap `mux`,
            # `frames_to_video` and `room_tone` all pin explicitly.
            "-f",
            "mp4",
            str(part_path),
        ]
    )
    part_path.replace(out_path)
    return out_path
