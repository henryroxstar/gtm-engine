from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SfxCue:
    """One timed cue.

    ``at_s`` is where the cue's TRANSIENT lands on the base clip's timeline — not where its file
    starts. ``trim_s`` is the transient's offset INSIDE the file (normally
    ``find_transient(...).onset_s``); the graph trims that head off first, so the two numbers
    compose without the caller doing the arithmetic by hand and getting it wrong by the trim.

    ``gain_db`` is relative to the cue's own peak. That is what makes the verification in
    :func:`mix_sfx_cues` possible at all: ``volume=NdB`` is a linear scalar, so the requested
    level in the mix is exactly ``cue_peak + gain_db``, a number that can be compared against a
    measurement.
    """

    path: str
    at_s: float
    gain_db: float = 0.0
    trim_s: float = 0.0
    label: str = ""


@dataclass(frozen=True)
class SfxCueMeasurement:
    label: str
    path: str
    at_s: float
    #: The cue file's own peak over the measured window, BEFORE gain.
    cue_peak_dbfs: float
    requested_dbfs: float
    #: Peak of the base-nulled residual in this cue's window — the cue alone, in the real mix.
    measured_dbfs: float
    shortfall_db: float
    #: Residual in a cue-free control window. -inf when the mix is too dense to find one, in
    #: which case the trust check is skipped rather than faked.
    null_floor_dbfs: float
    window: tuple[float, float]
    verified: bool


@dataclass(frozen=True)
class SfxMixResult:
    out_path: Path
    cues: tuple[SfxCueMeasurement, ...]
    filtergraph: str
    base_duration_s: float
    verified: bool
