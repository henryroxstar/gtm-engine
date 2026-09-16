from __future__ import annotations

import json
import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ..video_lint import SAFE_AREAS
from .constants import DEFAULT_GOP, DEFAULT_PRESET, DEFAULT_THREADS
from .errors import FfmpegUnavailable
from .ffmpeg import _probe_duration, _run_ffmpeg
from .plan import sidecar_path


@dataclass(frozen=True)
class BurnedShot:
    """One shot whose captions were burned in, with the evidence of what was burned and why."""

    shot_id: str
    src_path: str
    out_path: str
    text: str
    #: "caption_text_override" or "spoken" — which field the burned words came from. Recorded
    #: because "the caption matches the VO" and "the caption was deliberately corrected away from
    #: the VO" must not look identical to a later reader.
    text_source: str
    screens: int
    duration_s: float
    #: The ACTUAL drawn block box per screen — {x, y, w, h} in frame pixels, straight from
    #: :func:`gtm_core.captions.render`. Exposed because ``video_lint``'s V3 (face-band overlap)
    #: and V11 (contrast) both measure THIS geometry, and a caller that has to guess it declares
    #: the whole safe box instead: 729px tall rather than the ~180px the type occupies. That
    #: over-broad box both invents a face-band overlap the captions do not have and makes V11
    #: average mostly un-scrimmed background, so the rule under-reports its own fix.
    boxes: tuple[dict, ...] = ()
    #: ``<out stem>.captions.json`` beside the burned file — the same geometry as ``boxes``, with
    #: each screen's window in seconds RELATIVE TO THIS SHOT, in the shape ``video_lint`` reads.
    #: ``stitch`` merges these onto the master's timeline and ``plan`` ingests the merged one, so
    #: the geometry survives the stitch instead of stopping here (see ``plan.SIDECAR_KINDS``).
    sidecar_path: str = ""


@dataclass(frozen=True)
class BurnResult:
    ratio: str
    burned: tuple[BurnedShot, ...]
    #: (shot_id, why) for every shot that produced no burn. A skip is always reported, never
    #: silent — an empty out_dir must be explainable.
    skipped: tuple[tuple[str, str], ...]


def _measure_backdrop_luma(src: Path, *, ratio: str, placement: str, at_s: float) -> float | None:
    """Mean relative luminance of the pixels this shot will put BEHIND its caption.

    Sampled from the shot's own midpoint, cropped to the half of the safe box the placement
    actually occupies — not the whole frame. A film that is dark overall but light exactly where
    the type lands (this one: a night library, then the app's near-white UI) is precisely the case
    a frame-wide average gets wrong, and getting it wrong here picks the wrong glyph colour.

    Returns ``None`` if ffmpeg or Pillow is unavailable, or the frame will not decode — the caller
    then renders at the prior default rather than failing the burn over a nice-to-have."""
    import subprocess  # nosec B404 — ffmpeg frame sampling, arg lists only, never shell=True

    from ..captions import backdrop_luma, caption_box_crop

    ffmpeg_bin = shutil.which("ffmpeg")
    box = caption_box_crop(ratio, placement)
    if ffmpeg_bin is None or box is None:
        return None
    w, h, x, y = box
    try:
        proc = subprocess.run(  # nosec B603 — arg list resolved via shutil.which, never shell=True
            [
                ffmpeg_bin,
                "-v",
                "error",
                "-ss",
                f"{max(at_s, 0.0):.3f}",
                "-i",
                str(src),
                "-frames:v",
                "1",
                "-vf",
                f"crop={w}:{h}:{x}:{y}",
                "-f",
                "image2pipe",
                "-vcodec",
                "png",
                "-",
            ],
            capture_output=True,
            check=True,
        )
    except Exception:  # noqa: BLE001 — measurement is advisory; never sink the burn
        return None
    return backdrop_luma(proc.stdout)


def _caption_spans_for_shot(
    shot_id: str,
    segments: list[dict] | None,
    override: str,
    spoken: str,
    duration_s: float,
) -> tuple[list[dict], str, str]:
    """Resolve one shot's caption spans, preferring ``caption_segments`` over a single span.

    A shot whose caption channel ALTERNATES between two speakers cannot be expressed as one
    span: the second line has to land at the moment the first speaker stops, and spreading one
    string evenly over the shot puts it wherever the word count falls. Windows are clamped to
    the probed duration for the same reason the span is: the declared number is what was asked
    for, the probe is what exists.
    """
    if not segments:
        text = override or spoken
        text_source = "caption_text_override" if override else "spoken"
        return [{"text": text, "start_s": 0.0, "end_s": duration_s}], text, text_source

    spans = []
    for n, seg in enumerate(segments):
        seg_text = str(seg.get("text") or "").strip()
        start_s = float(seg.get("start_s", 0.0))
        end_s = float(seg.get("end_s", duration_s))
        if not seg_text:
            raise ValueError(f"shot {shot_id!r} caption_segments[{n}] has no text")
        if not 0.0 <= start_s < end_s:
            raise ValueError(
                f"shot {shot_id!r} caption_segments[{n}] window [{start_s}, {end_s}] is "
                "not a forward span starting at or after zero"
            )
        spans.append(
            {
                "text": seg_text,
                "start_s": min(start_s, duration_s),
                "end_s": min(end_s, duration_s),
            }
        )
    return spans, " ".join(sp["text"] for sp in spans), "caption_segments"


def burn_captions(
    shots: Sequence[dict],
    *,
    ratio: str,
    kit: dict,
    out_dir: Path,
    workdir: Path,
    shots_root: Path,
    repo_root: Path | None = None,
    max_words: int = 5,
    video_bitrate: str = "16M",
    shot_ids: Sequence[str] | None = None,
) -> BurnResult:
    """Burn each shot's caption into that shot's OWN file, before the stitch.

    This is the glue that was hand-written into three throwaway scripts in a single session on
    2026-08-30 — split the line into screens, render them to transparent PNGs, overlay them with
    a filtergraph, re-encode. Written once here, it is testable; written three times in
    ``content/``, it was state.

    TWO LOAD-BEARING PROPERTIES.

    1. **Per shot, never onto the assembled master.** Absolute offsets are not recoverable by
       arithmetic across a concat: the demuxer's output measured **0.637s longer** than the sum of
       its own inputs' video durations, because container/AAC padding differs from stream
       duration. A caption timed against the master therefore drifts roughly half a second late by
       the closing card, and the drift accumulates monotonically so the error is largest exactly
       where the call to action is. A window computed inside a single shot cannot drift out of
       that shot, whatever the concat later does to the timeline. This is the same reasoning that
       puts the SFX cue mix before the stitch (:func:`mix_sfx_cues`).

    2. **``caption_text_override`` wins over ``spoken``.** The closing VO says "acme dot com"
       because that is how a voice reads a URL; the burned caption must read "acme.com"
       because that is what a viewer can type. The two are correct and different, and only a field
       that says so can express it. Owning the caption text also beats transcribing the audio: an
       ASR pass over this same film silently dropped a word from one shot, and a caption derived
       from that transcript would have shipped the drop as if it were the script.

    The shot's window is ``[0, D]`` where **D is probed off the shot's own file**, not read from
    the shot list's ``duration_s`` — the declared number is what was asked for, the probe is what
    exists, and property 1 above is exactly about the gap between those two.

    Never writes over its input: each burn lands at ``out_dir/<shot_id>-captioned.mp4``.
    """
    from ..captions import (
        overlay_filtergraph,
        render,
        resolve_placement,
        sidecar_payload,
        split_screens_segmented,
    )

    if ratio not in SAFE_AREAS:
        raise ValueError(f"unknown ratio {ratio!r} — expected one of {sorted(SAFE_AREAS)}")
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")
    out_dir.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)

    wanted = set(shot_ids) if shot_ids else None
    if wanted:
        # Same fallback the loop below uses. Deriving it twice differently meant an
        # id-less shot was "shot-00" to the loop and "" to this check, so filtering for
        # it was refused as unknown.
        known = {str(sh.get("id") or f"shot-{i:02d}") for i, sh in enumerate(shots)}
        missing = sorted(wanted - known)
        if missing:
            raise ValueError(
                f"--shot-ids names {missing} which are not in this shot list "
                f"(it has {sorted(k for k in known if k)}). A filter that silently matches "
                "nothing looks exactly like a clean run over zero shots."
            )

    burned: list[BurnedShot] = []
    skipped: list[tuple[str, str]] = []

    for i, shot in enumerate(shots):
        shot_id = str(shot.get("id") or f"shot-{i:02d}")
        if wanted is not None and shot_id not in wanted:
            continue

        spoken = str(shot.get("spoken") or "").strip()
        override = str(shot.get("caption_text_override") or "").strip()
        # EITHER field is sufficient. Until 2026-09-03 this required `spoken` and skipped before
        # `caption_text_override` was ever read below — so a CAPTIONS-ONLY cut burned nothing and
        # reported every shot skipped. That shape is not exotic: `shots_lint` refuses a `spoken`
        # line on a profile whose kit has no `identity.voice_id` and tells the author to move the
        # line into the caption and clear `spoken`, which is precisely the shape this then
        # refused. Two halves of one pipeline disagreed, and the failure surfaced only as an
        # empty out_dir at the last step before delivery.
        segments = shot.get("caption_segments") or None
        if not spoken and not override and not segments:
            skipped.append(
                (
                    shot_id,
                    "no 'spoken', 'caption_text_override' or 'caption_segments' — "
                    "nothing to caption",
                )
            )
            continue

        file_rel = str(shot.get("file") or "").strip()
        if not file_rel:
            skipped.append((shot_id, "no 'file' field — this shot has not been rendered yet"))
            continue

        src = (shots_root / file_rel).resolve()
        if not src.is_file():
            # A refusal, not a skip. A stale path means the shot list and the disk disagree, and
            # continuing would produce an out_dir that looks like a complete pass.
            raise ValueError(
                f"shot {shot_id!r} names file {file_rel!r} which does not exist at {src}"
            )

        duration_s = _probe_duration(src, stream_selector="v:0")
        # `split_screens_segmented` has always taken segments — only this caller once hardcoded
        # a single `[0, D]` one; see `_caption_spans_for_shot` for why segments win.
        spans, text, text_source = _caption_spans_for_shot(
            shot_id, segments, override, spoken, duration_s
        )
        screens = split_screens_segmented(spans, max_words=max_words)
        if not screens:
            skipped.append((shot_id, "caption text produced no screens"))
            continue

        png_dir = workdir / f"captions-{shot_id}"
        placement = resolve_placement(kit, ratio=ratio)
        backdrop_luma = _measure_backdrop_luma(
            src, ratio=ratio, placement=placement, at_s=duration_s / 2.0
        )
        rendered = render(
            screens,
            ratio=ratio,
            kit=kit,
            out_dir=png_dir,
            repo_root=repo_root,
            placement=placement,
            backdrop_luma=backdrop_luma,
        )
        graph = overlay_filtergraph(rendered)

        out_path = out_dir / f"{shot_id}-captioned.mp4"
        part_path = out_path.with_suffix(out_path.suffix + ".part")
        args = [ffmpeg_bin, "-v", "error", "-y", "-i", str(src)]
        for r in rendered:
            args += ["-i", str(r.png_path)]
        args += [
            "-filter_complex",
            graph,
            "-map",
            "[vout]",
            # Optional: a shot whose VO has not been muxed yet still burns, it just carries no
            # audio. Refusing here would make the caption pass depend on the mux order.
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            str(DEFAULT_PRESET),
            "-b:v",
            str(video_bitrate),
            "-pix_fmt",
            "yuv420p",
            "-g",
            str(DEFAULT_GOP),
            "-threads",
            str(DEFAULT_THREADS),
            # Stream-copy the audio: this pass has no business re-encoding a VO it did not change.
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            # The intermediate is <name>.mp4.part, so ffmpeg cannot infer the muxer from the
            # extension — same reason every other verb in this module pins it explicitly.
            "-f",
            "mp4",
            str(part_path),
        ]
        _run_ffmpeg(args)
        os.replace(part_path, out_path)

        payload = {**sidecar_payload(rendered, ratio=ratio), "shot_id": shot_id, "text": text}
        sidecar = sidecar_path(out_path, "captions")
        sidecar.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        burned.append(
            BurnedShot(
                shot_id=shot_id,
                src_path=str(src),
                out_path=str(out_path),
                text=text,
                text_source=text_source,
                screens=len(rendered),
                duration_s=round(duration_s, 6),
                boxes=tuple(dict(r.box) for r in rendered),
                sidecar_path=str(sidecar),
            )
        )

    return BurnResult(ratio=ratio, burned=tuple(burned), skipped=tuple(skipped))
