from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import FfmpegUnavailable, SfxError
from .ffmpeg import _measure_window_dbfs, _parse_max_volume, _run_ffmpeg_capture
from .sfx_cues import SfxCue

#: The fine-grained sweep :func:`find_transient` uses. A 50 ms window is what a transient's energy
#: actually occupies; a 10 ms hop resolves the reported onset to 10 ms while each measurement
#: still integrates over enough samples to be stable. A 500 ms search costs ~50 ffmpeg calls on a
#: sub-second file, which is free next to one wrong cue.
SFX_TRANSIENT_WINDOW_S = 0.050
SFX_TRANSIENT_HOP_S = 0.010
SFX_TRANSIENT_SEARCH_S = 0.500

#: The transient starts at the first window within this much of the file's OWN peak. RELATIVE,
#: never absolute — see :func:`find_transient` for why an absolute threshold was the defect.
SFX_TRANSIENT_REL_FLOOR_DB = 6.0

#: A file whose loudest window in the whole search span is below this is not a cue, it is silence.
SFX_MIN_CUE_PEAK_DBFS = -50.0

#: How far a verified cue must sit above the null residual before its measurement means anything.
SFX_NULL_TRUST_MARGIN_DB = 10.0


@dataclass(frozen=True)
class Transient:
    path: str
    #: START of the first qualifying window — never the peak position. See :func:`find_transient`.
    onset_s: float
    peak_s: float
    peak_dbfs: float
    window_s: float
    hop_s: float
    #: The decision rule, rendered, so a failure message can quote what produced this number.
    rule: str


def find_transient(
    path: Path,
    *,
    search_s: float = SFX_TRANSIENT_SEARCH_S,
    window_s: float = SFX_TRANSIENT_WINDOW_S,
    hop_s: float = SFX_TRANSIENT_HOP_S,
    rel_floor_db: float = SFX_TRANSIENT_REL_FLOOR_DB,
    min_peak_dbfs: float = SFX_MIN_CUE_PEAK_DBFS,
) -> Transient:
    """Where a cue file's sound actually starts, measured with a fine-grained level sweep.

    WHY NOT ``silencedetect``, WHICH IS WHAT WAS USED AND WHAT FAILED. ``silencedetect`` is an
    ABSOLUTE detector: ``noise=-35dB:d=0.05`` answers "where does this file exceed -35 dBFS for
    at least 50 ms", which is only the same question as "where does the sound start" when the
    file's peak sits comfortably above -35 dB. The cue that shipped broken peaked around
    -34 dBFS, so the threshold cut through the middle of the signal: the reported ``silence_end``
    tracked whichever later moment happened to sustain 50 ms above the line (0.18s), the real
    attack at 0.10-0.15s fell inside a span the detector called silence, the head was trimmed
    past the attack, and what shipped was a decay tail at -35.8 dBFS.

    The rule here is RELATIVE — the first window within ``rel_floor_db`` of the file's OWN peak —
    so a cue 40 dB quieter than another yields the same onset. That is the property an absolute
    threshold does not have and cannot be tuned into having.

    Returns the WINDOW START, not the peak position. Trimming at the peak amputates the attack,
    the 5-20 ms of rise that makes a click read as a click; the window start is a guaranteed
    lead on it.
    """
    steps = max(1, int((search_s - window_s) / hop_s) + 1)
    windows = [
        (round(i * hop_s, 6), _measure_window_dbfs(path, start_s=i * hop_s, duration_s=window_s))
        for i in range(steps)
    ]
    peak_s, peak_dbfs = max(windows, key=lambda w: w[1])
    if peak_dbfs < min_peak_dbfs:
        raise SfxError(
            f"no transient in the first {search_s}s of {path}: the loudest {window_s * 1000:.0f} ms "
            f"measures {peak_dbfs:.1f} dBFS, below the {min_peak_dbfs} dBFS floor. This file is "
            "silence, or its sound starts later than the search span."
        )
    rule = (
        f"first {window_s * 1000:.0f} ms window within {rel_floor_db:.1f} dB of the file's own "
        f"{peak_dbfs:.1f} dBFS peak"
    )
    onset_s = next(t for t, db in windows if db >= peak_dbfs - rel_floor_db)
    return Transient(
        path=str(path),
        onset_s=onset_s,
        peak_s=peak_s,
        peak_dbfs=peak_dbfs,
        window_s=window_s,
        hop_s=hop_s,
        rule=rule,
    )


def _control_window(
    cues: Sequence[SfxCue], *, base_duration_s: float, span_s: float, guard_s: float = 0.05
) -> tuple[float, float] | None:
    """The first cue-free stretch long enough to measure the null residual's floor in.

    Returns None when the mix is too dense to hold one. That is reported as an -inf floor and a
    SKIPPED trust check, never as a fabricated number: a control window that overlaps a cue would
    read as a high noise floor and turn every real cue into a false "unverifiable".
    """
    busy = sorted((max(0.0, c.at_s - guard_s), c.at_s + span_s + guard_s) for c in cues)
    cursor = 0.0
    for start, end in busy:
        if start - cursor >= span_s:
            return (cursor, span_s)
        cursor = max(cursor, end)
    if base_duration_s - cursor >= span_s:
        return (cursor, span_s)
    return None


def _null_residual_dbfs(
    mix_wav: Path, base_wav: Path, *, start_s: float, duration_s: float
) -> float:
    """Peak of (mix - base) over one window: the cues alone, with the base REMOVED rather than
    estimated. ``volume=volume=-1:precision=float`` is a linear gain of -1, i.e. a polarity
    inversion, and summing an inverted copy is subtraction. The two wavs come from one graph and
    one decode, so they are sample-aligned by construction — which is the assumption every
    shortfall number rests on, and why the control window is MEASURED rather than assumed."""
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")
    graph = (
        "[1:a]volume=volume=-1:precision=float[inv];"
        "[0:a][inv]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
        f"atrim=start={max(0.0, start_s):.4f}:end={max(0.001, start_s + duration_s):.4f},"
        "asetpts=PTS-STARTPTS,volumedetect"
    )
    log = _run_ffmpeg_capture(
        [
            ffmpeg_bin,
            "-hide_banner",
            "-nostats",
            "-v",
            "info",
            "-i",
            str(mix_wav),
            "-i",
            str(base_wav),
            "-filter_complex",
            graph,
            "-f",
            "null",
            "-",
        ]
    )
    return _parse_max_volume(log)
