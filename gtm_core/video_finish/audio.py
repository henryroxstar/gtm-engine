from __future__ import annotations

import math
import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from .errors import FfmpegUnavailable
from .ffmpeg import _probe_duration, _run_ffmpeg


def duck_music_bed(
    voice_path: Path,
    music_path: Path,
    *,
    out_path: Path,
    workdir: Path,
    duck_threshold: float = 0.05,
    duck_ratio: float = 8.0,
    music_gain_db: float = -15.0,
) -> Path:
    """Sidechain-compress ``music_path``'s audio against ``voice_path``'s audio (ffmpeg's
    ``sidechaincompress`` filter — present in the operator's local build, verified live, unlike
    F1's drawtext/libass gap) so the music ducks under speech, then mix the ducked music with the
    voice into one AAC stream. Reads only the AUDIO stream from each input (either may be a full
    video file); returns an audio-only ``.m4a``, not a muxed video — the caller mixes it onto the
    finished visual separately.

    ``sidechaincompress`` expects matching sample rate / channel layout on both inputs; this
    function does not resample or probe for that — mismatched inputs fail loudly at the ffmpeg
    call rather than silently producing wrong-sounding output.

    THE VOICE COMES OUT AT UNITY, AND THAT IS THE POINT OF ``normalize=0``. ffmpeg's ``amix``
    defaults to scaling every input by ``1/n``, so mixing a bed under a finished voice track
    silently costs the VOICE 6 dB — the bed is not laid *under* the speech, both are pulled
    down together. Measured on the 2026-08-29 long-form master: a voice track at -21.3 dBFS RMS
    came out of this function at -27.2. Nothing errors, nothing looks wrong in a waveform, and
    the loss is invisible to :mod:`gtm_core.video_lint`, whose dead-air gate only asks whether
    the floor is high enough — adding a bed RAISES the floor while quietly lowering the speech,
    so the gate goes greener as the mix gets worse. The bed's level is therefore set explicitly
    by ``music_gain_db`` (relative to its own source level) and the voice is never attenuated:
    a level decision belongs in a named parameter, not in a filter's default.

    ``music_gain_db`` is applied BEFORE the compressor, so the ducking depth quoted by
    ``duck_ratio`` still describes what it sounds like it describes.
    """
    if out_path.suffix != ".m4a":
        raise ValueError(f"out_path must end in .m4a, got {out_path.name!r}")
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")
    workdir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = out_path.with_suffix(out_path.suffix + ".part")

    # The sidechain input is named explicitly ([1:a] main = music, [0:a] sidechain = voice).
    # ffmpeg auto-connects the second input when only one is labelled, and a live A/B
    # (2026-08-29) measured an IDENTICAL duck depth either way — so this is the implicit
    # wiring written down, not a behaviour change. It is written down because "which stream
    # is the sidechain" is the one thing a reader of this filtergraph must not have to guess.
    filtergraph = (
        f"[1:a]volume={music_gain_db}dB[bed];"
        f"[bed][0:a]sidechaincompress=threshold={duck_threshold}:ratio={duck_ratio}:"
        "attack=5:release=300[ducked];[0:a][ducked]amix=inputs=2:duration=first:"
        "dropout_transition=0:normalize=0[aout]"
    )
    args = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(voice_path),
        "-i",
        str(music_path),
        "-filter_complex",
        filtergraph,
        "-map",
        "[aout]",
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


def _db_envelope_expr(breakpoints: Sequence[tuple[float, float]]) -> str:
    """A piecewise-linear dB automation curve as an ffmpeg ``volume`` expression.

    ``breakpoints`` are ``(time_s, gain_db)`` in the SOURCE track's own timeline, because the
    thing being corrected — a cue's built-in swell — is a property of the track, not of the bed
    built from it. Interpolation is linear in dB (which is what the ear reads as a smooth ride),
    and the curve holds flat at the first and last values outside the given range.
    """
    pts = sorted(breakpoints)
    if not pts:
        raise ValueError("level_envelope needs at least one (time_s, gain_db) breakpoint")
    if any(t < 0 for t, _ in pts):
        raise ValueError("level_envelope times must be >= 0")

    expr = f"pow(10,{pts[-1][1]}/20)"
    for (t0, d0), (t1, d1) in zip(reversed(pts[:-1]), reversed(pts[1:]), strict=True):
        if t1 <= t0:
            raise ValueError(f"level_envelope times must strictly increase (got {t0} then {t1})")
        ramp = f"pow(10,({d0}+({d1}-{d0})*(t-{t0})/({t1}-{t0}))/20)"
        expr = f"if(lt(t,{t1}),{ramp},{expr})"
    return f"if(lt(t,{pts[0][0]}),pow(10,{pts[0][1]}/20),{expr})"


def build_music_bed(
    track_path: Path,
    *,
    out_path: Path,
    duration_s: float,
    workdir: Path,
    body_end_s: float | None = None,
    loop_from_s: float = 0.0,
    crossfade_s: float = 4.0,
    fade_out_s: float = 3.0,
    level_envelope: Sequence[tuple[float, float]] | None = None,
) -> Path:
    """Extend one catalogue music track into a bed of exactly ``duration_s``.

    WHY THIS IS NOT A ONE-OFF. Licensed catalogue tracks are written to be *cues*, and the
    long ones in HeyGen's catalogue top out around 137s (measured 2026-08-29). Any long-form
    film outruns them — a 2:39 cut needs 23s more bed than the longest track on offer — so
    "the score is shorter than the picture" is the normal case for this lane, not an accident
    of one edit. Doing it by hand means ad-hoc ffmpeg outside :func:`_run_ffmpeg`, which is
    exactly the unreviewed-pixel-work path this module exists to close.

    Two things make the seam inaudible, and both are the caller's to get right:

    * ``body_end_s`` trims the track's OWN fade-out before anything is repeated. A catalogue
      cue almost always ends by fading to silence; loop past that point and the bed dips to
      nothing in the middle of the film. Measure where the body ends — do not assume the file
      length is usable material.
    * ``loop_from_s`` picks where the repeat is taken from, so it can be LEVEL-MATCHED to the
      splice point. These beds are written with a slow build (the track used on 2026-08-29
      rises about 6 dB across two minutes), so looping back to 0:00 drops the bed several dB
      at the seam and the ear hears the join even through a crossfade. Take the repeat from a
      passage already sitting at the level the splice lands on.

    ``level_envelope`` rides the cue's own dynamics before any of that happens. Catalogue
    music is written to be *listened to*, so it swells; a bed is written to be *under*
    something, so it must not. The track used on 2026-08-30 rises 5.8 dB between 0:45 and
    1:10 — gradually, so no jump-detector fires, but enough that the same ducker setting is
    correct at the top of the film and wrong in the middle. Flattening it here rather than
    leaning harder on the ducker keeps the duck depth a fixed, measurable quantity instead of
    something that drifts with the arrangement. Breakpoints are in the SOURCE track's timeline.

    The result is trimmed to exactly ``duration_s`` and given its own ``fade_out_s`` tail —
    the film's ending, not the track's.
    """
    if out_path.suffix != ".m4a":
        raise ValueError(f"out_path must end in .m4a, got {out_path.name!r}")
    if duration_s <= 0:
        raise ValueError(f"duration_s must be positive, got {duration_s}")
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")

    track_len = _probe_duration(track_path, stream_selector="a:0")
    body_end = track_len if body_end_s is None else min(body_end_s, track_len)
    if body_end <= 0:
        raise ValueError(f"body_end_s resolves to {body_end}s of usable audio")
    if not 0 <= loop_from_s < body_end:
        raise ValueError(f"loop_from_s={loop_from_s} is outside the usable body (0..{body_end})")

    # How many repeats of [loop_from_s, body_end] it takes to cover the shortfall. Each
    # crossfade eats `crossfade_s` of total length, so the effective gain per repeat is
    # shorter than the section itself.
    section_len = body_end - loop_from_s
    gain_per_repeat = section_len - crossfade_s
    if duration_s > body_end and gain_per_repeat <= 0:
        raise ValueError(
            f"a {section_len:.1f}s loop section cannot extend anything with a "
            f"{crossfade_s:.1f}s crossfade — lower crossfade_s or loop_from_s"
        )
    repeats = 0 if duration_s <= body_end else math.ceil((duration_s - body_end) / gain_per_repeat)

    # Each repeat is materialized to its own file and crossfaded in a SEPARATE ffmpeg process,
    # never chained inside one -filter_complex: on ffmpeg 6.1 (Ubuntu 24.04's apt build, the CI
    # runner — measured 2026-08-31) a second acrossfade fed by the first's output silently
    # truncates to roughly one section's length instead of erroring, so the defect is invisible
    # until something probes the output duration. A single acrossfade per process always measured
    # correct on both that build and ffmpeg 8.1 (this machine's).
    envelope_prefix = (
        f"volume=volume='{_db_envelope_expr(level_envelope)}':eval=frame," if level_envelope else ""
    )

    workdir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def _materialize(start: float, end: float, dest: Path) -> Path:
        filt = f"[0:a]{envelope_prefix}atrim={start}:{end},asetpts=PTS-STARTPTS[aout]"
        _run_ffmpeg(
            [
                ffmpeg_bin,
                "-y",
                "-i",
                str(track_path),
                "-filter_complex",
                filt,
                "-map",
                "[aout]",
                "-ac",
                "2",
                "-ar",
                "48000",
                "-c:a",
                "pcm_s16le",
                str(dest),
            ]
        )
        return dest

    cur = _materialize(0.0, body_end, workdir / "bed_seg0.wav")
    if repeats:
        # Every repeat re-trims the identical [loop_from_s, body_end] span, so materialize it
        # once and crossfade it in repeatedly rather than re-rendering the same audio N times.
        loop_seg = _materialize(loop_from_s, body_end, workdir / "bed_loop.wav")
        for i in range(1, repeats + 1):
            nxt = workdir / f"bed_x{i}.wav"
            _run_ffmpeg(
                [
                    ffmpeg_bin,
                    "-y",
                    "-i",
                    str(cur),
                    "-i",
                    str(loop_seg),
                    "-filter_complex",
                    f"[0:a][1:a]acrossfade=d={crossfade_s}:c1=tri:c2=tri[aout]",
                    "-map",
                    "[aout]",
                    "-ac",
                    "2",
                    "-ar",
                    "48000",
                    "-c:a",
                    "pcm_s16le",
                    str(nxt),
                ]
            )
            cur = nxt

    fade_start = max(0.0, duration_s - fade_out_s)
    part_path = out_path.with_suffix(out_path.suffix + ".part")
    _run_ffmpeg(
        [
            ffmpeg_bin,
            "-y",
            "-i",
            str(cur),
            "-filter_complex",
            f"[0:a]atrim=0:{duration_s},asetpts=PTS-STARTPTS,"
            f"afade=t=out:st={fade_start}:d={fade_out_s}[aout]",
            "-map",
            "[aout]",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            # ffmpeg cannot infer a muxer from a `.part` suffix; name it (same trap as
            # room_tone's `.m4a.part`, which exited 234 until `-f ipod` was pinned).
            "-f",
            "ipod",
            str(part_path),
        ]
    )
    os.replace(part_path, out_path)
    return out_path


def room_tone(
    *,
    out_path: Path,
    duration_s: float,
    amplitude: float = 0.09,
    lowpass_hz: int = 340,
) -> Path:
    """Synthesize a low, flat room-tone bed as a 48kHz stereo ``.m4a``.

    WHY THIS IS SYNTHESIZED RATHER THAN SOURCED. ``video_lint`` V10's own remedy for dead air is
    "room tone under everything" — and room tone genuinely IS filtered low-level noise, so making
    it is not a substitute for a real recording the way generated music would be a substitute for
    a real track. It also needs no egress: the provider music catalogues we can reach return
    pre-signed URLs on hosts outside ``media_fetch``'s §R6 allowlist, and Higgsfield's own tool
    contract forbids its music models for standalone use.

    This is NOT a score. A score is a creative asset with a shape, and this deliberately has none
    — it is the noise floor that stops a cut sounding like a broken file. Do not let its presence
    read as "the audio pass is finished" when a bed was called for.

    ``amplitude`` is the pre-filter noise amplitude, not a dBFS target — the lowpass removes most
    of the energy. Measured at this default (2026-08-29, 340Hz lowpass): **-44.3 dBFS RMS**, which
    is the point. ``video_lint`` V10 counts anything under **-50 dBFS** as dead air, so a bed has
    to sit above that line to do its job; the first build used 0.014 and delivered -60.5 dBFS, so
    it was laid under the whole film and changed the dead-air measurement by exactly nothing. A
    bed you cannot measure is a bed that is not there. Reference points: 0.06 -> -47.9 dBFS
    (too close to the line), 0.09 -> -44.3, 0.13 -> -41.1.
    """
    if out_path.suffix != ".m4a":
        raise ValueError(f"out_path must end in .m4a, got {out_path.name!r}")
    if duration_s <= 0:
        raise ValueError(f"duration_s must be > 0, got {duration_s}")
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = out_path.with_suffix(out_path.suffix + ".part")
    args = [
        ffmpeg_bin,
        "-y",
        "-f",
        "lavfi",
        "-i",
        # seed pinned: two runs of the same inputs produce the same bed, the same property
        # every frame-drawing path in this repo keeps.
        f"anoisesrc=color=pink:sample_rate=48000:amplitude={amplitude}:seed=1729",
        "-t",
        f"{duration_s:.3f}",
        "-af",
        f"lowpass=f={lowpass_hz},highpass=f=35",
        "-ac",
        "2",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        # Muxer named explicitly: the atomic-write temp path ends in `.part`, and ffmpeg infers
        # the container from the extension, so it cannot find one without this.
        "-f",
        "ipod",
        str(part_path),
    ]
    _run_ffmpeg(args)
    os.replace(part_path, out_path)
    return out_path


def fit_audio_duration(
    src: Path,
    *,
    duration_s: float,
    out_path: Path,
    workdir: Path,
    atempo_max: float = 1.15,
) -> Path:
    """Force one audio file to exactly ``duration_s`` — pad with trailing silence if it runs
    short, time-compress (never hard-trim) if it runs long. Neither ``mux()`` nor
    ``duck_music_bed()`` covers this: mux assumes the VO nearly fills its target (a 0.35s
    truncate ceiling, a 1.15x atempo ceiling), which holds for a lip-synced shot but not for
    narration that is DELIBERATELY shorter than the picture it plays under — a VO ending before
    the closing card so the card can breathe is a normal edit choice, not a mismatch to correct.
    ``duck_music_bed`` mixes with ``duration=first``, so its voice and music inputs must already
    agree on length before it runs; this is what makes them agree, cheaply, once, rather than
    teaching either function to guess an alignment.

    A source that runs OVER is compressed with ``atempo``, same transparency ceiling ``mux()``
    uses (1.15x), never hard-trimmed to length: a narration track ends on real words, and cutting
    its tail to fit is the exact defect ``mux()`` already refuses for a lip-synced shot, just
    reached from a different caller. Past the ceiling this raises rather than trim anyway — that
    is a script-length problem, the same call ``mux()`` makes.

    A source that runs SHORT is padded with trailing silence (``apad`` then ``atrim`` to lock the
    exact length) — silence at the end of a VO track is not a defect, it is where the picture
    keeps playing under nothing but the bed.
    """
    if out_path.suffix != ".m4a":
        raise ValueError(f"out_path must end in .m4a, got {out_path.name!r}")
    if duration_s <= 0:
        raise ValueError(f"duration_s must be > 0, got {duration_s}")
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")
    workdir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = out_path.with_suffix(out_path.suffix + ".part")

    src_s = _probe_duration(src, stream_selector=None)
    if src_s > duration_s:
        required = src_s / duration_s
        if required > atempo_max:
            raise ValueError(
                f"source is {src_s:.2f}s against a {duration_s:.2f}s target — fitting it would "
                f"need atempo={required:.3f}, past the {atempo_max} transparency ceiling. "
                "Shorten the script; do not hard-trim spoken words to length."
            )
        audio_filter = f"atempo={required:.6f}"
    else:
        audio_filter = f"apad=whole_dur={duration_s:.3f},atrim=0:{duration_s:.3f}"
    audio_filter += ",asetpts=PTS-STARTPTS"

    args = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(src),
        "-af",
        audio_filter,
        "-ac",
        "2",
        "-ar",
        "48000",
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
