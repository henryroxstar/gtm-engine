from __future__ import annotations

import math
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .constants import (
    AUDIO_CHANNELS,
    AUDIO_SAMPLE_RATE,
    MUX_ATEMPO_MAX,
    MUX_ATEMPO_MAX_LIP_SYNCED,
    MUX_TRUNCATE_MAX_S,
)
from .errors import FfmpegUnavailable, PlanError
from .ffmpeg import _probe_duration, _run_ffmpeg


@dataclass(frozen=True)
class MuxResult:
    """What :func:`mux` actually did — recorded rather than inferred, because the audio/video
    duration relationship decides whether the spoken line survives intact."""

    out_path: Path
    video_duration_s: float
    audio_duration_s: float
    strategy: str  # "truncate-video" | "atempo"
    atempo: float | None = None


def _suggest_duration(
    audio_s: float,
    *,
    atempo_max: float = MUX_ATEMPO_MAX,
    truncate_max_s: float = MUX_TRUNCATE_MAX_S,
) -> int | None:
    """The integer render duration that lets :func:`mux` accept this VO, or ``None`` if none does.

    Providers take an integer duration, so the two ceilings above bracket a *band* of legal
    lengths rather than a point: a duration is legal when the leftover silence is within
    ``truncate_max_s`` (``d >= audio_s - truncate_max_s``) or the VO compresses to fit within
    ``atempo_max`` (``d >= audio_s / atempo_max``).

    That band can be empty. A 3.60s VO admits no integer: 3s needs atempo 1.18 (past 1.15) and 4s
    leaves a 0.40s tail (past 0.35). Returning ``None`` rather than a plausible-looking number is
    the point — the earlier message computed ``round(audio_s)``, which for 3.60s suggested the
    very 4s duration that had just been refused, sending the reader in a circle.
    """
    lo = max(2, math.ceil(audio_s / atempo_max - 1e-9))
    hi = math.floor(audio_s + truncate_max_s)
    return lo if lo <= hi else None


def mux(
    video_path: Path,
    audio_path: Path,
    *,
    out_path: Path,
    workdir: Path,
    audio_bitrate: str = "192k",
    atempo_max: float = MUX_ATEMPO_MAX,
    truncate_max_s: float = MUX_TRUNCATE_MAX_S,
    lip_synced: bool = False,
) -> MuxResult:
    """Attach one VO track to one silent rendered shot, refusing to lose words silently.

    ``video-render`` generates each shot with ``generate_audio: false`` and passes the VO only as
    ``audio_references`` (which drives mouth motion, not the output audio track), so every rendered
    shot arrives SILENT and its real audio has to be attached here. Video comes from input 0 and
    audio from input 1 explicitly, so a source with no audio stream at all is the normal case, not
    an error.

    The duration relationship is the whole reason this is a function and not a one-line ffmpeg call:

    * **audio <= video, within ``truncate_max_s``** → ``strategy="truncate-video"``. ``-shortest``
      ends the output when the VO ends, trimming the short silent tail rather than holding a
      motionless presenter through dead air.
    * **audio <= video, tail past ``truncate_max_s``** → :class:`PlanError`. This is the *other*
      half of the lip-sync defect and it used to pass silently, because discarding silence looks
      lossless. It isn't: the model already spread a full shot's worth of mouth motion across a
      duration the audio never fills, so the visible mouth runs slow from frame one and trimming
      the end cannot recover it. The fix is upstream — re-render the shot at the VO's own length.
    * **audio > video, within ``atempo_max``** → ``strategy="atempo"``. The VO is time-compressed to
      fit. ``atempo`` is transparent at small ratios, which is what makes this safe *only* while
      bounded.
    * **audio > video, past ``atempo_max``** → :class:`PlanError`. This is a script-length problem,
      not a mux-time one: ``-shortest`` here would cut the end off the spoken line (on a payoff beat
      that silently deletes the whole point of the video), and a large ``atempo`` audibly distorts
      the cloned voice. The error names both real fixes — re-render the shot longer, or shorten the
      line — instead of picking a lossy one.
    """
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")
    if atempo_max < 1.0:
        raise ValueError(f"atempo_max must be >= 1.0, got {atempo_max}")
    if lip_synced:
        atempo_max = min(atempo_max, MUX_ATEMPO_MAX_LIP_SYNCED)

    video_s = _probe_duration(video_path)
    audio_s = _probe_duration(audio_path, stream_selector=None)

    workdir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = workdir / f"{out_path.name}.part"

    args = [ffmpeg_bin, "-y", "-i", str(video_path), "-i", str(audio_path)]

    if audio_s <= video_s:
        tail_s = video_s - audio_s
        if tail_s > truncate_max_s:
            suggestion = _suggest_duration(
                audio_s, atempo_max=atempo_max, truncate_max_s=truncate_max_s
            )
            if suggestion is None:
                remedy = (
                    f"No integer duration fits this VO: {math.floor(audio_s + truncate_max_s)}s "
                    f"would need atempo>{atempo_max} and "
                    f"{math.floor(audio_s + truncate_max_s) + 1}s leaves a tail past "
                    f"{truncate_max_s}s. Re-roll the VO (TTS length varies run to run) or adjust "
                    "the line — do not widen a ceiling to land in the gap between them."
                )
            else:
                remedy = (
                    f"Re-render this shot at duration={suggestion}s (integer, matching the VO) "
                    "instead of a fixed length"
                )
            raise PlanError(
                f"the shot is {video_s:.2f}s but its VO is only {audio_s:.2f}s — a {tail_s:.2f}s "
                f"silent tail, past the {truncate_max_s}s ceiling. Trimming it does NOT fix the "
                "result: the model spread a full 5s-shot's worth of mouth motion across a "
                "duration the audio never fills, so the mouth runs slow from the first frame and "
                f"reads as broken lip sync however the tail is cut. {remedy} — see "
                "gtm_core.shots_lint's VO/duration parity check, which catches this before any "
                "spend."
            )
        strategy = "truncate-video"
        atempo = None
        args += ["-map", "0:v", "-map", "1:a", "-shortest"]
    else:
        required = audio_s / video_s
        if required > atempo_max:
            raise PlanError(
                f"VO is {audio_s:.2f}s but the shot is only {video_s:.2f}s — fitting it would need "
                f"atempo={required:.3f}, past the {atempo_max} transparency ceiling"
                + (
                    " for a LIP-SYNCED shot — the mouth was animated against this VO's original "
                    "timing, so rescaling the audio slides the words off the lips by that same "
                    "factor"
                    if lip_synced
                    else ""
                )
                + ". This is a script-length problem, not a mux problem: re-render the shot "
                "longer (an integer duration the model accepts) or shorten the spoken line. "
                "Refusing to truncate the end of the line or audibly distort the cloned voice to "
                "paper over it."
            )
        strategy = "atempo"
        atempo = required
        args += [
            "-filter:a",
            f"atempo={required:.6f}",
            "-map",
            "0:v",
            "-map",
            "1:a",
        ]

    args += [
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-ar",
        str(AUDIO_SAMPLE_RATE),
        "-ac",
        str(AUDIO_CHANNELS),
        "-f",
        "mp4",
        str(part_path),
    ]
    _run_ffmpeg(args)
    os.replace(part_path, out_path)

    return MuxResult(
        out_path=out_path,
        video_duration_s=video_s,
        audio_duration_s=audio_s,
        strategy=strategy,
        atempo=atempo,
    )


def frames_to_video(
    frames_glob: str,
    *,
    fps: int,
    out_path: Path,
    silent_audio: bool = True,
) -> Path:
    """Encode a numbered PNG frame sequence (e.g. ``gtm_core.screen_ui``'s output) into a
    silent mp4, via the sole sanctioned ffmpeg entry point (:func:`_run_ffmpeg`).

    ``frames_glob`` is an ffmpeg-style pattern (``shot2-booking-%04d.png``), matching the
    zero-padded naming :mod:`gtm_core.screen_ui` writes. This is a THIRD reason a raw frame
    sequence must not be turned into video from outside this module (alongside grading and
    captions): ``Bash(ffmpeg:*)`` is denied precisely so a hand-authored encode cannot bypass
    this module's invariants, and a locally-drawn UI mockup is no more exempt from that than a
    provider's render is.

    ``silent_audio`` adds a silent stereo track (ffmpeg's ``anullsrc``) matched to the video's
    duration. `mux()` and `stitch()` both assume every segment carries an audio stream — a video-
    only file desyncs a downstream concat that expects one, so this defaults to on rather than
    leaving that failure for someone to rediscover at stitch time.
    """
    if fps <= 0:
        raise ValueError(f"fps must be > 0, got {fps}")
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = out_path.with_suffix(out_path.suffix + ".part")

    args = [ffmpeg_bin, "-y", "-framerate", str(fps), "-i", frames_glob]
    if silent_audio:
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16"]
    if silent_audio:
        args += ["-c:a", "aac", "-shortest"]
    args += ["-f", "mp4", str(part_path)]

    _run_ffmpeg(args)
    os.replace(part_path, out_path)
    return out_path


def overlay_frames(
    base_path: Path,
    frames_glob: str,
    *,
    fps: int,
    out_path: Path,
    video_crf: int = 16,
) -> Path:
    """Composite an RGBA PNG sequence over a finished shot, keeping the shot's own audio.

    This is how a locally-drawn UI element lands on top of a PROVIDER render — the case the
    ``screen_ui`` card scenes cannot serve, because a card is a full frame of its own and a chip
    has to sit on someone's face-shot without replacing it. Alpha is honoured, so the overlay
    sequence must have been written with ``_write_frames(..., alpha=True)``; a flattened RGB
    sequence composites as an opaque rectangle, which is a silent, obvious-in-hindsight way to
    black out the picture underneath.

    The overlay is looped if it is shorter than the base and cut when the base ends, so the
    caller sizes the sequence to the shot rather than to the frame count.
    """
    if fps <= 0:
        raise ValueError(f"fps must be > 0, got {fps}")
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = out_path.with_suffix(out_path.suffix + ".part")
    _run_ffmpeg(
        [
            ffmpeg_bin,
            "-y",
            "-i",
            str(base_path),
            "-framerate",
            str(fps),
            "-loop",
            "1",
            "-i",
            frames_glob,
            "-filter_complex",
            "[0:v][1:v]overlay=x=0:y=0:shortest=1:format=auto[v]",
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            str(video_crf),
            "-c:a",
            "copy",
            "-f",
            "mp4",
            str(part_path),
        ]
    )
    os.replace(part_path, out_path)
    return out_path


def inset_video(
    base_path: Path,
    inset_path: Path,
    *,
    rect: tuple[int, int, int, int],
    out_path: Path,
    start_s: float = 0.0,
    audio_from: str = "inset",
    video_crf: int = 16,
) -> Path:
    """Scale ``inset_path`` into ``rect`` on top of ``base_path``. The sibling of
    :func:`overlay_frames` for the case where the thing going on top is FOOTAGE, not a drawn
    sequence.

    ``overlay_frames`` composites a locally-drawn RGBA sequence over a provider render at 1:1.
    This is the inverse layering: a drawn full-frame ground (a call-row UI, say) underneath, and a
    provider render placed as one tile inside it. Neither is expressible as the other — an RGBA
    sequence cannot carry moving footage, and a card scene cannot leave a hole for it, because the
    overlay always lands on top.

    ``rect`` is ``(x, y, w, h)`` in the base frame's own pixels. The inset is fitted INSIDE that
    box preserving its aspect ratio rather than stretched to it: a caller shot squeezed by a few
    percent reads as a subtly wrong face, which is the kind of defect that survives QA.

    ``start_s`` delays the inset — the base shows through until then. That is what lets a shot
    open on its own UI settling before the footage cuts in, without a second compositing pass to
    cover the tile.

    The inset's last frame is CLONED to the end of the base rather than the tile going black when
    the footage runs out. A beat that holds on the speaker after they finish talking is the point
    of the hold; an empty rectangle is a bug. The output ends when the BASE ends, so the base is
    what states the shot's duration.
    """
    x, y, w, h = rect
    if w <= 0 or h <= 0:
        raise ValueError(f"rect w/h must be > 0, got {w}x{h}")
    if x < 0 or y < 0:
        raise ValueError(f"rect x/y must be >= 0, got ({x}, {y})")
    if start_s < 0:
        raise ValueError(f"start_s must be >= 0, got {start_s}")
    if audio_from not in {"inset", "base"}:
        raise ValueError(f"audio_from must be 'inset' or 'base', got {audio_from!r}")
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = out_path.with_suffix(out_path.suffix + ".part")

    # `stop_duration` is a hold long enough to outlast any base this is used with; `shortest=1`
    # on the overlay is what actually ends the output.
    fit = (
        f"[1:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
        f"setpts=PTS-STARTPTS+{start_s}/TB,"
        f"tpad=stop_mode=clone:stop_duration=3600[ins];"
    )
    over = f"[0:v][ins]overlay=x={x}:y={y}:shortest=1:enable='gte(t,{start_s})':format=auto[v]"
    filters = fit + over
    maps = ["-map", "[v]"]
    if audio_from == "inset":
        delay_ms = round(start_s * 1000)
        filters += f";[1:a]adelay={delay_ms}|{delay_ms},apad[a]"
        maps += ["-map", "[a]"]
    else:
        maps += ["-map", "0:a?"]

    _run_ffmpeg(
        [
            ffmpeg_bin,
            "-y",
            "-i",
            str(base_path),
            "-i",
            str(inset_path),
            "-filter_complex",
            filters,
            *maps,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            str(video_crf),
            "-c:a",
            "aac",
            "-ar",
            str(AUDIO_SAMPLE_RATE),
            "-ac",
            str(AUDIO_CHANNELS),
            "-shortest",
            "-f",
            "mp4",
            str(part_path),
        ]
    )
    os.replace(part_path, out_path)
    return out_path
