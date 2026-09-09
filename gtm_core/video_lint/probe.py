from __future__ import annotations

import json
import subprocess  # nosec B404 — ffprobe orchestration, arg list only, never shell=True
from dataclasses import dataclass
from pathlib import Path


class ProbeUnavailable(RuntimeError):
    """ffprobe is not on PATH."""


class ProbeFailed(RuntimeError):
    """ffprobe ran but the input could not be probed (corrupt, zero-byte, not media)."""


@dataclass(frozen=True)
class Probe:
    width: int
    height: int
    fps: float
    duration_s: float
    bit_rate: int | None  # bits/sec; None when neither stream nor format reports one
    has_audio: bool = True
    pix_fmt: str = ""

    @classmethod
    def from_ffprobe_json(cls, payload: dict) -> Probe:
        streams = payload.get("streams", [])
        # An attached cover image reports codec_type=video; it is not the picture track. Prefer a
        # real video stream and only fall back to the naive pick if that is all there is.
        vstreams = [s for s in streams if s.get("codec_type") == "video"]
        vstream = next(
            (s for s in vstreams if not (s.get("disposition") or {}).get("attached_pic")),
            next(iter(vstreams), None),
        )
        if vstream is None:
            raise ProbeFailed("no video stream in ffprobe output")
        fmt = payload.get("format", {})
        rate_raw = vstream.get("r_frame_rate", "0/1")
        num, _, den = rate_raw.partition("/")
        try:
            fps = float(num) / float(den) if den and float(den) != 0 else float(num)
        except ValueError:
            fps = 0.0
        bit_rate_raw = vstream.get("bit_rate") or fmt.get("bit_rate")
        duration_raw = fmt.get("duration") or vstream.get("duration")
        has_audio = any(s.get("codec_type") == "audio" for s in streams)
        try:
            return cls(
                width=int(vstream.get("width", 0)),
                height=int(vstream.get("height", 0)),
                fps=fps,
                duration_s=float(duration_raw) if duration_raw is not None else 0.0,
                bit_rate=int(bit_rate_raw) if bit_rate_raw is not None else None,
                has_audio=has_audio,
                pix_fmt=str(vstream.get("pix_fmt", "")),
            )
        except (TypeError, ValueError) as exc:
            raise ProbeFailed(f"malformed ffprobe stream/format fields: {exc}") from exc


def probe(path: Path) -> Probe:
    """The only ffprobe shell-out in this module. Raises ProbeUnavailable if ffprobe is not on
    PATH, ProbeFailed if the input cannot be probed (corrupt, zero-byte, not media, no video
    stream). Never returns a clean result for a file that could not actually be measured."""
    import shutil

    ffprobe_bin = shutil.which("ffprobe")
    if ffprobe_bin is None:
        raise ProbeUnavailable("ffprobe not found on PATH")
    try:
        out = subprocess.run(  # nosec B603 — arg list resolved via shutil.which, never shell=True
            [
                ffprobe_bin,
                "-v",
                "error",
                # NOT -select_streams v:0. It was, until 2026-08-28, and it filtered the audio
                # stream out of the payload — so ``has_audio`` was unconditionally False on every
                # asset ever probed. from_ffprobe_json picks the video stream itself, so the
                # selector was redundant for its stated purpose and wrong for this one.
                "-show_entries",
                "stream=width,height,r_frame_rate,bit_rate,codec_type,pix_fmt,duration,disposition",
                "-show_entries",
                "format=duration,bit_rate",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProbeFailed(f"ffprobe could not run: {exc}") from exc
    if out.returncode != 0 or not out.stdout.strip():
        raise ProbeFailed(f"ffprobe exited {out.returncode}: {out.stderr.strip()[:500]}")
    try:
        payload = json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeFailed(f"ffprobe produced non-JSON output: {exc}") from exc
    return Probe.from_ffprobe_json(payload)
