from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from .constants import AUDIO_CHANNELS, AUDIO_SAMPLE_RATE
from .errors import FfmpegUnavailable, SfxError
from .ffmpeg import _measure_window_dbfs, _probe_duration, _probe_stream_kinds, _run_ffmpeg
from .sfx_cues import SfxCue, SfxCueMeasurement, SfxMixResult
from .sfx_detect import SFX_NULL_TRUST_MARGIN_DB, _control_window, _null_residual_dbfs

# ── Timed SFX cues ───────────────────────────────────────────────────────────────────────
#
# Two defects from one pass on 2026-08-31, both invisible to every probe-based gate:
#
#   1. `amix` defaults to `normalize=true`, scaling EVERY input by 1/n. On the five-way mix that
#      film needed (a base plus four cues) that is ~14 dB of attenuation which nothing reports
#      and no waveform looks wrong for. `duck_music_bed` already documents the two-input case;
#      these constants and this graph exist so the N-input case cannot repeat it.
#   2. `silencedetect` at -35 dB / 50 ms picked an onset AFTER the real transient. The cue was
#      trimmed from 0.18s when the true attack was in the 0.10-0.15s window, and it shipped at
#      -35.8 dBFS — present in the file, inaudible in the room.
#
# The answer to both is the same: measure, do not trust the filtergraph.

#: How far below its requested level a mixed cue may land before the mix is REFUSED. 6 dB is not
#: a taste number — it is exactly one `amix` normalize division (1/2 -> -6.02 dB). At or past it,
#: the cause is gain staging, not resampler ringing or rounding.
SFX_SHORTFALL_MAX_DB = 6.0


def _sfx_format_chain() -> str:
    """The uniform layout every branch of an SFX mix is forced to.

    `amix` requires identical sample rate and channel layout across its inputs; the constants are
    the module's existing ones, because a 44.1 kHz mono cue beside a 48 kHz stereo base is the
    same defect ``AUDIO_SAMPLE_RATE`` was written down for.
    """
    layout = "stereo" if AUDIO_CHANNELS == 2 else "mono"
    return f"aresample={AUDIO_SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts={layout}"


def _build_sfx_filtergraph(
    cues: Sequence[SfxCue], *, base_label: str = "0:a", tap_base: bool = False
) -> str:
    """Pure: the filter_complex for a base plus N timed cues. No ffmpeg, no filesystem.

    Every element is load-bearing:

    * ``asetpts=PTS-STARTPTS`` AFTER each ``atrim`` — ``atrim`` preserves source PTS, so without
      the reset a cue trimmed at 0.120 already starts at t=0.120 and ``adelay=1500`` lands it at
      1.620s. Silent, plausible, and wrong by exactly the trim.
    * the format chain on EVERY branch, base included (see :func:`_sfx_format_chain`).
    * ``volume={gain}dB`` after the format normalisation and before the delay — a linear scalar,
      so the cue's peak moves by exactly ``gain_db``. That identity is the entire basis of the
      verification arithmetic, and it only holds once resampling has happened.
    * ``adelay={ms}|{ms}`` — ONE VALUE PER CHANNEL. A bare ``adelay=4200`` delays channel 1 only
      and leaves the right channel at zero, so the cue arrives as a 4.2-second stereo smear.
    * ``duration=first`` — the base is input 0, so the mix is exactly the clip's length and a cue
      near the tail is truncated rather than extending the file past its own picture.
    * ``normalize=0`` — NON-NEGOTIABLE. ``amix`` divides by its input count, so three cues cost
      the base 12.04 dB, not 6. It also degrades exactly the way ``duck_music_bed``'s docstring
      describes: ``video_lint``'s dead-air gate only asks whether the floor is high enough, and
      adding cues RAISES the floor while lowering the speech, so the gate goes greener as the
      mix gets worse.
    """
    fmt = _sfx_format_chain()
    tail = "asplit=2[base][baseout]" if tap_base else "[base]"
    joiner = "," if tap_base else ""
    parts = [f"[{base_label}]{fmt},asetpts=PTS-STARTPTS{joiner}{tail}"]
    labels = ["base"]
    for i, cue in enumerate(cues, start=1):
        delays = "|".join([str(int(round(cue.at_s * 1000)))] * AUDIO_CHANNELS)
        parts.append(
            f"[{i}:a]atrim=start={cue.trim_s:.6f},asetpts=PTS-STARTPTS,{fmt},"
            f"volume={cue.gain_db}dB,adelay={delays}[c{i}]"
        )
        labels.append(f"c{i}")
    chain = "".join(f"[{lbl}]" for lbl in labels)
    parts.append(
        f"{chain}amix=inputs={len(labels)}:duration=first:dropout_transition=0:normalize=0[aout]"
    )
    return ";".join(parts)


def _validate_sfx_cues(cues: Sequence[SfxCue], *, base_duration_s: float) -> None:
    if not cues:
        raise SfxError("mix_sfx_cues() needs at least one cue")
    seen: set[str] = set()
    for i, cue in enumerate(cues):
        name = cue.label or f"cue[{i}]"
        if cue.label:
            if cue.label in seen:
                raise SfxError(
                    f"duplicate cue label {cue.label!r} — labels name a cue in the failure "
                    "message and in the manifest, so two cues may not share one"
                )
            seen.add(cue.label)
        if cue.at_s < 0:
            raise SfxError(f"{name}: at_s={cue.at_s} is before the clip starts")
        if cue.at_s >= base_duration_s:
            raise SfxError(
                f"{name}: at_s={cue.at_s} is at or past the clip's {base_duration_s:.3f}s end — "
                "the cue would be truncated to nothing by duration=first"
            )
        if cue.trim_s < 0:
            raise SfxError(f"{name}: trim_s={cue.trim_s} is negative")


def mix_sfx_cues(
    clip: Path,
    cues: Sequence[SfxCue],
    *,
    out_path: Path,
    workdir: Path,
    verify: bool = True,
    shortfall_max_db: float = SFX_SHORTFALL_MAX_DB,
    measure_window_s: float = 0.200,
    keep_intermediates: bool = False,
) -> SfxMixResult:
    """Mix N timed cues onto a clip's EXISTING audio, and MEASURE that they landed.

    Mix cues onto each shot BEFORE the stitch, for the same reason
    :func:`burn_captions` burns captions per shot: a concat's output measured 0.637s longer than
    the sum of its inputs' video durations, so a cue timed against the assembled master drifts.

    VERIFICATION IS A MEASUREMENT, NOT A READ-BACK OF THE FILTERGRAPH. Three passes:

    1. Build the mix as PCM and TAP the aligned base out of the same graph, in one call. PCM, not
       AAC: a codec round trip leaves -40 to -60 dBFS of error, which puts a floor under every
       measurement and makes a quiet-but-correct cue indistinguishable from a missing one.
    2. Per cue, subtract the base by summing an inverted copy of it
       (``volume=volume=-1:precision=float`` into ``amix … normalize=0``), trim to the cue's
       window, and measure. **The base's contribution is REMOVED, never estimated.** Peaks do not
       subtract — two signals in a window can reinforce or cancel — so no arithmetic recovers a
       cue's level from ``mix_peak`` and ``base_peak``. Any rule of the form "the cue must sit
       6 dB above the local base peak" is a MIXING rule wearing a verification's clothes, and it
       would forbid the legitimate case of a soft tick placed deliberately under speech.
    3. Only then encode onto the picture, with ``-c:v copy``. Mixing sound must never re-encode a
       graded picture.

    A cue more than ``shortfall_max_db`` below its requested level RAISES, and because the
    verification happens before pass 3, a refused mix has never written a file a caller could
    mistake for done.
    """
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")
    has_video, has_audio = _probe_stream_kinds(clip)
    if not has_audio:
        raise SfxError(
            f"{clip} has no audio stream — mix_sfx_cues() mixes cues ONTO an existing track. "
            "Mux the shot's VO (or a room tone) first; a cue over nothing is a new track, not a "
            "mix, and calling it one hides that the VO never arrived."
        )
    expected_suffix = ".mp4" if has_video else ".m4a"
    if out_path.suffix != expected_suffix:
        raise SfxError(
            f"out_path must end in {expected_suffix} for a "
            f"{'video' if has_video else 'audio-only'} source, got {out_path.name!r}"
        )

    base_duration_s = _probe_duration(clip, stream_selector="a:0")
    _validate_sfx_cues(cues, base_duration_s=base_duration_s)

    workdir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mix_wav = workdir / "sfx-mix.wav"
    base_wav = workdir / "sfx-base.wav"

    graph = _build_sfx_filtergraph(cues, tap_base=True)
    inputs: list[str] = ["-i", str(clip)]
    for cue in cues:
        inputs += ["-i", str(cue.path)]
    pcm = [
        "-c:a",
        "pcm_s16le",
        "-ar",
        str(AUDIO_SAMPLE_RATE),
        "-ac",
        str(AUDIO_CHANNELS),
        "-f",
        "wav",
    ]
    _run_ffmpeg(
        [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-v",
            "error",
            *inputs,
            "-filter_complex",
            graph,
            "-map",
            "[aout]",
            *pcm,
            str(mix_wav),
            "-map",
            "[baseout]",
            *pcm,
            str(base_wav),
        ]
    )

    measurements: list[SfxCueMeasurement] = []
    if verify:
        control = _control_window(cues, base_duration_s=base_duration_s, span_s=measure_window_s)
        null_floor = (
            _null_residual_dbfs(mix_wav, base_wav, start_s=control[0], duration_s=control[1])
            if control
            else float("-inf")
        )
        for i, cue in enumerate(cues):
            name = cue.label or f"cue[{i}]"
            cue_peak = _measure_window_dbfs(
                Path(cue.path), start_s=max(0.0, cue.trim_s - 0.010), duration_s=measure_window_s
            )
            requested = cue_peak + cue.gain_db
            w0 = max(0.0, cue.at_s - 0.010)
            w1 = min(base_duration_s, cue.at_s + measure_window_s)
            measured = _null_residual_dbfs(mix_wav, base_wav, start_s=w0, duration_s=w1 - w0)
            shortfall = requested - measured
            trusted = measured >= null_floor + SFX_NULL_TRUST_MARGIN_DB
            ok = trusted and shortfall <= shortfall_max_db
            measurements.append(
                SfxCueMeasurement(
                    label=name,
                    path=str(cue.path),
                    at_s=cue.at_s,
                    cue_peak_dbfs=cue_peak,
                    requested_dbfs=requested,
                    measured_dbfs=measured,
                    shortfall_db=shortfall,
                    null_floor_dbfs=null_floor,
                    window=(round(w0, 4), round(w1, 4)),
                    verified=ok,
                )
            )
            if not trusted:
                _cleanup_sfx(workdir, keep=keep_intermediates)
                raise SfxError(
                    f"sfx cue {name!r} ({cue.path}) measures {measured:.1f} dBFS at {cue.at_s:.3f}s "
                    f"against a null floor of {null_floor:.1f} dBFS — within "
                    f"{SFX_NULL_TRUST_MARGIN_DB:.1f} dB of the arithmetic floor, so this is not a "
                    "measurement of the cue at all. Either the cue is absent from the window, or "
                    "at_s does not point where the cue actually landed. Nothing was written; "
                    f"{out_path} does not exist."
                )
            if shortfall > shortfall_max_db:
                _cleanup_sfx(workdir, keep=keep_intermediates)
                raise SfxError(
                    f"sfx cue {name!r} ({cue.path}) asked for {cue.gain_db:+.1f} dB against its own "
                    f"{cue_peak:.1f} dBFS peak (= {requested:.1f} dBFS requested in the mix) but the "
                    f"base-nulled residual measures {measured:.1f} dBFS at {cue.at_s:.3f}s "
                    f"(window {w0:.3f}-{w1:.3f}s, null floor {null_floor:.1f} dBFS): "
                    f"{shortfall:.1f} dB short, past the {shortfall_max_db:.1f} dB ceiling. That is "
                    "the inaudible-cue failure, not resampler ringing. Check, in this order: the "
                    f"trim (trim_s={cue.trim_s:.3f} — is it past the attack?), the delay "
                    f"(adelay={int(round(cue.at_s * 1000))}), and whether amix still carries "
                    f"normalize=0. Nothing was written; {out_path} does not exist."
                )

    part_path = out_path.with_suffix(out_path.suffix + ".part")
    encode = [ffmpeg_bin, "-y", "-hide_banner", "-v", "error", "-i", str(clip), "-i", str(mix_wav)]
    if has_video:
        # Never re-encode the picture to change the sound: that is a second generation loss on a
        # graded master, and it breaks execute()'s byte-identity contract.
        encode += ["-map", "0:v", "-map", "1:a", "-c:v", "copy"]
    else:
        encode += ["-map", "1:a"]
    encode += [
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        str(AUDIO_SAMPLE_RATE),
        "-ac",
        str(AUDIO_CHANNELS),
        "-shortest",
        "-f",
        "mp4" if has_video else "ipod",
        str(part_path),
    ]
    _run_ffmpeg(encode)
    os.replace(part_path, out_path)
    _cleanup_sfx(workdir, keep=keep_intermediates)

    return SfxMixResult(
        out_path=out_path,
        cues=tuple(measurements),
        filtergraph=graph,
        base_duration_s=base_duration_s,
        # NEVER `not verify`: with --no-verify there are no measurements, and "nothing
        # measured" is the one thing this field must not report as verified. A silent cue
        # mixed with --no-verify used to come back {"verified": true, "cues": []}, which is
        # precisely the false assurance the whole verification pass exists to prevent.
        verified=bool(measurements) and all(m.verified for m in measurements),
    )


def _cleanup_sfx(workdir: Path, *, keep: bool) -> None:
    if keep:
        return
    for name in ("sfx-mix.wav", "sfx-base.wav"):
        (workdir / name).unlink(missing_ok=True)
