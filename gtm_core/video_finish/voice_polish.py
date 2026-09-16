"""``polish_voice()`` — its own module because ``audio.py`` is at the §R10 cap.

Split rather than ceiling-raised (docs/RULES.md §R10): ``audio.py`` was already at its 500-line
cap for unlisted files when this arrived, and a ratchet answered by moving the ratchet is not a
ratchet. Same seam the package already uses for a single command that would otherwise push a
module over its cap — see ``cli_narration.py``'s docstring for the sibling case on the CLI side.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 — one ffprobe read, arg list only, never shell=True
from pathlib import Path

from .errors import FfmpegUnavailable
from .ffmpeg import _probe_duration, _run_ffmpeg

_DEESSER_FILTER = "deesser=i=0.2:m=0.3:f=0.5:s=o"


def _probe_audio_format(path: Path) -> tuple[int, int]:
    """(sample_rate, channels) of one audio stream. Sibling of the probes in :mod:`.ffmpeg` —
    same discipline (``shutil.which``, an arg list, never ``shell=True``) — kept local because
    no other function needs it yet.
    """
    ffprobe_bin = shutil.which("ffprobe")
    if ffprobe_bin is None:
        raise FfmpegUnavailable("ffprobe is not on PATH")
    out = subprocess.run(  # nosec B603 — arg list resolved via shutil.which, never shell=True
        [
            ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate,channels",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    stream = json.loads(out.stdout)["streams"][0]
    return int(stream["sample_rate"]), int(stream["channels"])


def polish_voice(
    src: Path,
    out: Path,
    *,
    highpass_hz: float = 80,
    mud_hz: float = 300,
    mud_db: float = -2.5,
    presence_hz: float = 4500,
    presence_db: float = 1.5,
    deess: bool = True,
    comp_threshold_db: float = -20,
    comp_ratio: float = 2.5,
    comp_attack_ms: float = 15,
    comp_release_ms: float = 250,
) -> dict:
    """Clean up one narration take in a SINGLE ffmpeg pass: highpass rumble, tame the ~300Hz
    mud, lift ~4.5kHz presence, de-ess sibilance, then a gentle compressor. Lossless WAV out
    (48kHz, 24-bit PCM), no loudness normalization — that stays ``execute()``'s ``loudnorm``
    stage, same as every other voice-only path in this module.

    Output is at the SOURCE's own channel count: nothing here resamples channels (no ``-ac``),
    and none of highpass/equalizer/deesser/acompressor change channel count on their own, so
    the source's layout simply passes through.

    NO TRIM, NO PAD, ANYWHERE IN THE CHAIN — deliberately. Word-level caption timings are built
    against this file's length, so the honest contract is that none of these filters (all
    sample-synchronous biquads/dynamics, none FFT/lookahead-based) change sample count or shift
    onset — never that a trim silently corrects it after the fact. A trim tail that only ever
    fires as a no-op is worse than none: it would hide the day a filter choice changes and starts
    adding real latency, instead of surfacing it as a duration/onset mismatch a caller can catch.

    ``deesser``'s ``i``/``m``/``f``/``s`` option names, ranges and defaults are verified against
    the ffmpeg filters reference (its ``deesser`` section, checked
    2026-09-14): ``i`` trigger intensity (0-1, default 0), ``m`` ducking amount on the sibilant
    band (0-1, default 0.5), ``f`` how much of the original content to keep while de-essing
    (0-1, default 0.5), ``s`` output mode (``i``/``o``/``e``, default ``o`` = the de-essed
    signal). Fixed here at ``i=0.2:m=0.3:f=0.5:s=o`` — a lighter ducking amount than the
    filter's own default, since this runs on every take rather than being dialed in per line.
    """
    if out.suffix != ".wav":
        raise ValueError(f"out must end in .wav, got {out.name!r}")
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")
    out.parent.mkdir(parents=True, exist_ok=True)
    part_path = out.with_suffix(out.suffix + ".part")

    src_s = _probe_duration(src, stream_selector=None)

    chain = [
        f"highpass=f={highpass_hz}",
        f"equalizer=f={mud_hz}:t=q:w=1.0:g={mud_db}",
        f"equalizer=f={presence_hz}:t=q:w=1.2:g={presence_db}",
    ]
    if deess:
        chain.append(_DEESSER_FILTER)
    chain.append(
        f"acompressor=threshold={comp_threshold_db}dB:ratio={comp_ratio}:"
        f"attack={comp_attack_ms}:release={comp_release_ms}:makeup=1"
    )

    args = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(src),
        "-af",
        ",".join(chain),
        "-ar",
        "48000",
        "-c:a",
        "pcm_s24le",
        "-fflags",
        "+bitexact",
        "-flags",
        "+bitexact",
        "-map_metadata",
        "-1",
        "-f",
        "wav",
        str(part_path),
    ]
    _run_ffmpeg(args)
    os.replace(part_path, out)

    sample_rate, channels = _probe_audio_format(out)
    return {
        "out_path": out,
        "input_duration_s": src_s,
        "output_duration_s": _probe_duration(out, stream_selector=None),
        "sample_rate": sample_rate,
        "channels": channels,
    }
