"""Build a film's VOICE-OVER MASTER from the narration lane of its shot list.

THIS IS THE READER THAT MAKES ``narration`` A CONTRACT. The lane landed in
``schemas/shots.schema.json`` as a place to record a read that had nowhere to live —
``spoken``/``vo_seconds`` model a per-shot lip-sync render request, not a track laid over a
finished cut — and ``gtm_core.shots_lint.narration`` checks it. But a field only a linter reads is
a field with no consequence: the onsets that actually placed the 2026-09-04 launch film's read
existed as numbers in a production note and in somebody's shell history, and the mux got them by
hand. Re-cut a line and nothing anywhere disagrees.

So this module places the read FROM the file. Two properties follow, and they are the point:

* **The declared numbers are checked against the bytes.** A line's voiced span
  (``lead_in_s + len_s``) must FIT inside its clip's probed duration, or the build is refused. A
  clip re-rendered shorter than the span the shot list claims stops being a comment that quietly
  rots and becomes an error at the moment it would otherwise mis-place a line. What this cannot
  catch, and does not pretend to: a clip that grew a longer TAIL, which is indistinguishable from
  a naturally long one without speech detection.
* **Placement is by onset, never by sequence.** Clips are delayed onto a common timeline, so a
  line that deliberately leads its picture cut keeps its lead, and the file's line order carries
  no meaning the mix depends on — the same property ``shots_lint`` relies on when it declines to
  have an opinion about ordering.
* **``voice_onset_s`` places the first WORD, so the clip is delayed by ``voice_onset_s -
  lead_in_s``.** This is the correction the lane was missing, and real data is what found it: the
  ten clips of the 2026-09-04 film run 0.245-0.391s longer than their declared ``len_s``, because
  ``len_s`` is the voiced span from the word timestamps and the FILE carries silence at both ends.
  Delay a clip by its onset and every first word lands late by its own lead-in — uneven, per line,
  which is drift rather than offset and so cannot be fixed downstream with one number. Same
  distinction :class:`gtm_core.video_finish.sfx_cues.SfxCue` draws between ``at_s`` and
  ``trim_s``, for the same reason.

THE BASE IS SILENCE, AND THAT IS NOT AN IMPLEMENTATION DETAIL. It would be easy to mix the read
onto :func:`gtm_core.video_finish.audio.room_tone` here and hand back one finished-sounding track.
That would be wrong: this file is the SIDECHAIN KEY for :func:`duck_music_bed`, which ducks the
music against whatever this track contains. Room tone in the key is signal that never stops, so
the compressor never releases and the bed sits ducked through the entire film — the 2026-09-03
"no music" defect arrived at by a different road. Room tone, beds and SFX are laid downstream of
this, against the picture. What this returns is voice and digital silence, nothing else.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .confine import _safe_asset_path
from .constants import AUDIO_CHANNELS, AUDIO_SAMPLE_RATE
from .errors import FfmpegUnavailable, PlanError
from .ffmpeg import _probe_duration, _run_ffmpeg

#: How far a clip's probed duration may sit from the ``len_s`` the shot list declares. This is
#: MEASUREMENT slack — a container rounds, and an AAC frame is 1024 samples (21ms at 48k) — not a
#: licence for a different take. Same magnitude as ``shots_lint.narration._NARRATION_TOLERANCE_S``
#: and deliberately a SEPARATE constant: that one is slack between two declared numbers, this one
#: is slack between a declared number and a file, and re-tuning either must not silently move the
#: other.
NARRATION_LEN_TOLERANCE_S = 0.05


class NarrationError(PlanError):
    """A narration track cannot be built as declared — a line names no audio, its file is missing,
    its measured length contradicts ``len_s``, or it would not fit the cut.

    Subclasses :class:`PlanError` so it inherits this module's existing CLI mapping to exit 4,
    the same reason :class:`SfxError` does.
    """


@dataclass(frozen=True)
class NarrationLine:
    """One line of the read, as the shot list declares it. ``file`` is relative to the build's
    base directory — the same convention as a shot's ``file``.

    ``lead_in_s`` is the first word's offset INSIDE that file, so the two numbers compose without
    the caller doing the arithmetic by hand: the clip is delayed by ``voice_onset_s - lead_in_s``
    and the word lands where the shot list says it does. It comes from the same word timestamps
    as ``voice_onset_s`` and ``len_s`` — the ones :mod:`gtm_core.vo_timings` persists as
    ``<id>.words.json`` — never from a detector at build time: a threshold choice moves every word
    by up to 0.1s, and this pipeline has no ground truth to pin one against.
    """

    line: str
    voice_onset_s: float
    len_s: float
    file: str
    lead_in_s: float = 0.0


@dataclass(frozen=True)
class NarrationLineMeasurement:
    """What a line DECLARED against what its audio actually is. ``index`` is 1-based and counts
    position in the shot list, so a finding names the place a reader has to go and edit."""

    index: int
    line: str
    file: str
    voice_onset_s: float
    lead_in_s: float
    declared_len_s: float
    #: The clip's whole probed duration — lead-in and tail included, so NOT comparable to
    #: ``declared_len_s``, which is the voiced span alone.
    measured_file_s: float

    @property
    def delay_s(self) -> float:
        """Where the clip's FILE starts on the master timeline."""
        return self.voice_onset_s - self.lead_in_s

    @property
    def tail_s(self) -> float:
        """Silence after the last word. Positive by construction — the build refuses otherwise."""
        return self.measured_file_s - self.lead_in_s - self.declared_len_s


@dataclass(frozen=True)
class NarrationTrackResult:
    out_path: Path
    duration_s: float
    lines: tuple[NarrationLineMeasurement, ...]
    filtergraph: str

    @property
    def speech_s(self) -> float:
        """Declared voiced time, summed. Deliberately NOT the summed file durations: those carry
        lead-in and tail, which are silence, and counting silence as speech would overstate the
        duty cycle by ~15% on a ten-line read — in the direction that makes a bed sound safer than
        it is, which is the wrong direction for the one number duck depth is chosen from."""
        return sum(m.declared_len_s for m in self.lines)

    def as_dict(self) -> dict:
        """What was BUILT, in the shape the CLI reports. Lives on the result rather than in the
        command handler: a caller embedding this in a manifest and a caller printing it must not
        drift into two descriptions of one build."""
        return {
            "out_path": str(self.out_path),
            "duration_s": self.duration_s,
            "speech_s": round(self.speech_s, 3),
            "speech_duty_pct": round(self.speech_duty_pct, 1),
            "lines": [
                {
                    "index": m.index,
                    "file": m.file,
                    "voice_onset_s": m.voice_onset_s,
                    "lead_in_s": m.lead_in_s,
                    "delay_s": round(m.delay_s, 3),
                    "declared_len_s": m.declared_len_s,
                    "measured_file_s": round(m.measured_file_s, 3),
                    "tail_s": round(m.tail_s, 3),
                }
                for m in self.lines
            ],
        }

    @property
    def speech_duty_pct(self) -> float:
        """The number duck depth scales inversely with, computed off the built track rather than
        read out of a note. ``shots_lint`` warns on the declared version before the build; this is
        the same figure after it, and a caller choosing ``duck_music_bed``'s depth should use it."""
        return 100.0 * self.speech_s / self.duration_s if self.duration_s > 0 else 0.0


def lines_from_shotlist(doc: dict) -> list[NarrationLine]:
    """Read the narration lane off a parsed shot list.

    Structural validity is ``gtm_core.shots_lint``'s job and the CLI runs it first; this refuses
    only the one thing the linter deliberately does not require, because a lane may legitimately
    record a read whose per-line audio was never kept: a ``file`` per line.
    """
    narration = doc.get("narration")
    lines = narration.get("lines") if isinstance(narration, dict) else None
    if not isinstance(lines, list) or not lines:
        raise NarrationError(
            "this shot list declares no `narration.lines` — there is no read to place. The "
            "per-shot `spoken` lane is a render request, not a track; see the lane's schema note."
        )
    missing = [
        i for i, entry in enumerate(lines, 1) if not str(entry.get("file", "") or "").strip()
    ]
    if missing:
        raise NarrationError(
            f"narration.lines {missing} name no `file` — the lane may record a read without one "
            "(a read mixed by hand keeps no per-line audio), but placing it needs the clips. Add "
            "`file` to each line, relative to the build's base directory, as a shot's `file` is."
        )
    return [
        NarrationLine(
            line=str(entry.get("line", "")),
            voice_onset_s=float(entry["voice_onset_s"]),
            len_s=float(entry["len_s"]),
            file=str(entry["file"]),
            lead_in_s=float(entry.get("lead_in_s", 0.0) or 0.0),
        )
        for entry in lines
    ]


def _measure_narration_lines(
    lines: Sequence[NarrationLine], *, base_dir: Path, total_duration_s: float
) -> tuple[NarrationLineMeasurement, ...]:
    """Resolve, confine and probe every clip BEFORE any encoding starts.

    All findings are collected and reported together. A read is placed as a whole, and fixing one
    stale ``len_s`` per ffmpeg round trip is how a ten-line film takes ten builds.
    """
    measurements: list[NarrationLineMeasurement] = []
    problems: list[str] = []
    for i, line in enumerate(lines, 1):
        try:
            resolved = _safe_asset_path(base_dir / line.file, content_root=base_dir)
        except Exception as exc:  # PolishError: outside the root, missing, or not a file
            problems.append(f"lines[{i}] ({line.file}): {exc}")
            continue
        measured = _probe_duration(resolved, stream_selector="a:0")
        measurements.append(
            NarrationLineMeasurement(
                index=i,
                line=line.line,
                file=line.file,
                voice_onset_s=line.voice_onset_s,
                lead_in_s=line.lead_in_s,
                declared_len_s=line.len_s,
                measured_file_s=measured,
            )
        )
        if line.lead_in_s < 0:
            problems.append(f"lines[{i}] lead_in_s={line.lead_in_s:g} is negative")
        span = line.lead_in_s + line.len_s
        if span > measured + NARRATION_LEN_TOLERANCE_S:
            problems.append(
                f"lines[{i}] claims a voiced span of {span:.3f}s (lead_in_s={line.lead_in_s:g} + "
                f"len_s={line.len_s:g}) but {line.file} is only {measured:.3f}s long — the clip "
                "cannot hold the line the shot list says it holds, so one of them was re-cut and "
                "the other was not. Re-measure the line against the clip it is now."
            )
        if line.voice_onset_s < line.lead_in_s:
            problems.append(
                f"lines[{i}] starts at {line.voice_onset_s:g}s but its clip carries "
                f"{line.lead_in_s:g}s of lead-in, so placing the first word there would need the "
                "file to begin before the film does. Move the onset later, or trim the clip's "
                "head and re-measure — this is an editorial choice, not one to make silently."
            )
        if line.voice_onset_s + line.len_s > total_duration_s + NARRATION_LEN_TOLERANCE_S:
            problems.append(
                f"lines[{i}] starts at {line.voice_onset_s:g}s and speaks for {line.len_s:g}s, "
                f"ending past the {total_duration_s:g}s cut — the mix would cut it mid-sentence."
            )
    if problems:
        raise NarrationError(
            "narration track refused; the shot list and its audio disagree:\n  "
            + "\n  ".join(problems)
        )
    return tuple(measurements)


def _build_narration_filtergraph(measurements: Sequence[NarrationLineMeasurement]) -> str:
    """``adelay`` each line onto a common timeline, then one ``amix``.

    Two ffmpeg contracts this repo has already paid to learn, both load-bearing here:

    * ``adelay={ms}|{ms}`` takes ONE VALUE PER CHANNEL. A bare ``adelay=450`` delays the left
      channel only and leaves the right at zero, which reads as a phasing artefact rather than as
      the timing bug it is.
    * The delay is ``voice_onset_s - lead_in_s``, never the onset: the onset places the first
      WORD and the delay places the FILE, whose head is silence.
    * ``normalize=0`` is NON-NEGOTIABLE. ``amix`` divides by its input count, so a ten-line read
      would come out 20 dB down — and uniformly, so nothing looks wrong until the whole film is
      quiet. Input 0 is the silent base, so the division would be by eleven, not ten.
    """
    chains = []
    for n, m in enumerate(measurements, 1):  # input 0 is the silent base
        delay_ms = int(round(m.delay_s * 1000))
        chains.append(f"[{n}:a]adelay={delay_ms}|{delay_ms}[v{n}]")
    labels = "[0:a]" + "".join(f"[v{n}]" for n in range(1, len(measurements) + 1))
    mix = (
        f"{labels}amix=inputs={len(measurements) + 1}:duration=first"
        f":dropout_transition=0:normalize=0[aout]"
    )
    return ";".join([*chains, mix])


def build_narration_track(
    lines: Sequence[NarrationLine],
    *,
    base_dir: Path,
    out_path: Path,
    total_duration_s: float,
) -> NarrationTrackResult:
    """Place a read onto one voice-only master of exactly ``total_duration_s``.

    Returns the measurements, so a caller can report what it built rather than what it was told.
    """
    if out_path.suffix != ".m4a":
        raise NarrationError(f"out_path must end in .m4a, got {out_path.name!r}")
    if total_duration_s <= 0:
        raise NarrationError(f"total_duration_s must be > 0, got {total_duration_s}")
    if not lines:
        raise NarrationError("build_narration_track() needs at least one line")

    measurements = _measure_narration_lines(
        lines, base_dir=base_dir, total_duration_s=total_duration_s
    )

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")

    graph = _build_narration_filtergraph(measurements)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = out_path.with_suffix(out_path.suffix + ".part")
    args = [ffmpeg_bin, "-y", "-f", "lavfi", "-i", f"anullsrc=r={AUDIO_SAMPLE_RATE}:cl=stereo"]
    for m in measurements:
        args += ["-i", str((base_dir / m.file).resolve())]
    args += [
        "-filter_complex",
        graph,
        "-map",
        "[aout]",
        "-t",
        f"{total_duration_s:.3f}",
        "-ac",
        str(AUDIO_CHANNELS),
        "-ar",
        str(AUDIO_SAMPLE_RATE),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        # Named explicitly: the atomic-write temp path ends in `.part`, and ffmpeg infers the
        # container from the extension. Same reason as room_tone().
        "-f",
        "ipod",
        str(part_path),
    ]
    _run_ffmpeg(args)
    os.replace(part_path, out_path)
    return NarrationTrackResult(
        out_path=out_path,
        duration_s=total_duration_s,
        lines=measurements,
        filtergraph=graph,
    )
