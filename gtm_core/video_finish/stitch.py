from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from ..video_lint import SAFE_AREAS
from .constants import DEFAULT_CRF, DEFAULT_GOP, DEFAULT_PRESET, DEFAULT_THREADS
from .errors import FfmpegUnavailable
from .ffmpeg import _probe_duration, _probe_fps, _run_ffmpeg
from .plan import SIDECAR_KINDS, TRANSITIONS_KIND, sidecar_path
from .shots import ShotSegment, _normalize_shot

#: A shot list's ``production.transition_in`` kind -> the xfade transition that renders it. A
#: hard cut is deliberately absent: it is the LACK of a transition, joined with the concat
#: filter, and the stream-copy path when every join is one. ``push`` maps to xfade's ``slideup``
#: — verified against the ffmpeg source (both frames are re-sampled by the same vertical offset,
#: unlike a wipe's static reveal line or a cover/reveal's single moving frame) and empirically in
#: test_video_finish_stitch_transitions.py: the outgoing frame slides UP and out, the incoming
#: frame slides UP into place from the bottom. xfade has no easing/interpolation-curve option
#: (only ``transition``/``duration``/``offset``/``expr``), so every kind — ``push`` included —
#: renders at xfade's native linear progress.
_XFADE_TRANSITIONS: dict[str, str] = {"dissolve": "fade", "fade": "fadeblack", "push": "slideup"}


def stitched_sidecars(out_path: Path) -> dict[str, str]:
    """The merged sidecars :func:`stitch` wrote beside ``out_path``, kind -> path.

    Discovery by the naming convention rather than a richer return type: :func:`stitch` returns
    the master's ``Path`` to every caller it has, and a stitch that found no segment sidecars
    removes any stale merged one, so what this finds is what the LAST stitch produced."""
    return {
        kind: str(path)
        for kind in SIDECAR_KINDS
        if (path := sidecar_path(out_path, kind)).is_file()
    }


def _resolve_joins(
    count: int, *, crossfade_s: float, transitions: list[tuple[str, float]] | None
) -> list[tuple[str, float]]:
    """One ``(kind, duration_s)`` per JOIN — ``count - 1`` of them, ``("cut", 0.0)`` for a hard
    cut. The single place the two ways of asking for a transition are reconciled.

    ``crossfade_s`` is the whole-film lever: every join becomes the same dissolve. ``transitions``
    is the per-join list a shot list's ``production.transition_in`` derives (see
    :func:`gtm_core.video_finish.shots.transitions_from_shots`). Passing both is refused rather
    than resolved — they are two answers to one question, and picking one silently is how a film
    gets dissolves the shot list never asked for.
    """
    if transitions is not None and crossfade_s > 0.0:
        raise ValueError(
            "pass crossfade_s (one dissolve on every join) or transitions (one entry per join), "
            "not both"
        )
    joins = max(count - 1, 0)
    if transitions is None:
        return [("dissolve", crossfade_s) if crossfade_s > 0.0 else ("cut", 0.0)] * joins
    if len(transitions) != joins:
        raise ValueError(
            f"transitions has {len(transitions)} entr(ies) but {count} segments have {joins} "
            "join(s) — one entry per join, in order, ('cut', 0.0) for a hard cut"
        )
    resolved: list[tuple[str, float]] = []
    for k, entry in enumerate(transitions):
        if not (isinstance(entry, (tuple, list)) and len(entry) == 2):
            raise ValueError(f"transitions[{k}] {entry!r} is not a (kind, duration_s) pair")
        kind, duration = str(entry[0]), float(entry[1])
        if kind == "cut":
            if duration != 0.0:
                raise ValueError(f"transitions[{k}] is a cut but asks for {duration}s of overlap")
        elif kind not in _XFADE_TRANSITIONS:
            raise ValueError(
                f"transitions[{k}] kind {kind!r} is not one of "
                f"{sorted(_XFADE_TRANSITIONS) + ['cut']}"
            )
        elif duration <= 0.0:
            raise ValueError(f"transitions[{k}] is a {kind} of {duration}s — a join needs time")
        resolved.append((kind, duration))
    return resolved


def _write_transitions_sidecar(
    segments: list[ShotSegment], joins: list[tuple[str, float]], *, out_path: Path
) -> None:
    """Record the non-cut joins as ``<out stem>.transitions.json``, so the master carries what
    was CUT INTO it and ``plan()`` can put it in the finish manifest — the same carry-forward
    :func:`_merge_sidecars` does for caption geometry, and what makes
    ``gtm_core.shots_coverage``'s finish stage able to tell a honoured dissolve from a dropped
    one. An all-cuts stitch writes nothing and clears a stale file from an earlier stitch."""
    target = sidecar_path(out_path, TRANSITIONS_KIND)
    rows = [
        {"join": k, "kind": kind, "duration_s": round(duration, 3), "into": segments[k + 1].path}
        for k, (kind, duration) in enumerate(joins)
        if kind != "cut"
    ]
    if not rows:
        target.unlink(missing_ok=True)
        return
    target.write_text(
        json.dumps({"joins": rows}, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _merge_sidecars(
    segment_paths: list[Path], normalized: list[Path], *, out_path: Path, overlaps: list[float]
) -> dict[str, str]:
    """Merge every ``<segment stem>.<kind>.json`` onto the master's timeline as
    ``<out stem>.<kind>.json``, kind -> written path.

    A window inside one shot cannot drift out of that shot, which is why captions are burned per
    shot (see :func:`burn_captions`). The cost of that choice is that nothing downstream knew the
    geometry: the finish manifest of a stitched, pre-burned master said ``captions: null`` and the
    lint's contrast tier never ran. This is the carry-forward. Each entry's ``start_s``/``end_s``
    is offset by the cumulative PROBED duration of the segments before it, MINUS the overlap every
    join before it consumed (``overlaps``, one per join, 0 for a hard cut — so a caption on the
    shot after a 0.4s dissolve lands 0.4s earlier than it would after a hard cut, and a per-join
    list is honoured join by join rather than as one film-wide crossfade);
    ``frame`` must agree across segments, because a box is only
    meaningful in the frame it was drawn at. The concat demuxer's own container padding can still
    put the master a few hundred ms long of the sum — the lint samples each screen's midpoint,
    which tolerates that; a frame mismatch it cannot tolerate, so that one is refused.

    A kind with no sidecar on any segment gets no merged file, and a stale one from an earlier
    stitch beside the same ``out_path`` is removed rather than left to describe a master that no
    longer exists."""
    found = {
        kind: [
            (i, path)
            for i, seg in enumerate(segment_paths)
            if (path := sidecar_path(seg, kind)).is_file()
        ]
        for kind in SIDECAR_KINDS
    }
    if not any(found.values()):
        for kind in SIDECAR_KINDS:
            sidecar_path(out_path, kind).unlink(missing_ok=True)
        return {}

    durations = [_probe_duration(p) for p in normalized]
    offsets = [sum(durations[:i]) - sum(overlaps[:i]) for i in range(len(durations))]
    written: dict[str, str] = {}
    for kind, list_key in SIDECAR_KINDS.items():
        target = sidecar_path(out_path, kind)
        hits = found[kind]
        if not hits:
            target.unlink(missing_ok=True)
            continue
        frame: list | None = None
        entries: list[dict] = []
        provenance: list[dict] = []
        for i, path in hits:
            payload = json.loads(path.read_text(encoding="utf-8"))
            seg_frame = payload.get("frame")
            if not (isinstance(seg_frame, list) and len(seg_frame) == 2):
                raise ValueError(f"{path} has no usable frame: {seg_frame!r} (expected [w, h])")
            if frame is None:
                frame = list(seg_frame)
            elif list(seg_frame) != frame:
                raise ValueError(
                    f"{kind} sidecars disagree on frame: {hits[0][1].name} says {frame}, "
                    f"{path.name} says {list(seg_frame)} — every segment must be burned at the "
                    "same frame before it is stitched, or its boxes describe a different picture"
                )
            for entry in payload.get(list_key) or []:
                if not isinstance(entry, dict):
                    continue
                shifted = dict(entry)
                for key in ("start_s", "end_s"):
                    if key in shifted:
                        shifted[key] = round(float(shifted[key]) + offsets[i], 3)
                shifted.setdefault("shot_id", payload.get("shot_id"))
                entries.append(shifted)
            provenance.append(
                {
                    "path": str(segment_paths[i]),
                    "shot_id": payload.get("shot_id"),
                    "offset_s": round(offsets[i], 3),
                    "duration_s": round(durations[i], 3),
                }
            )
        for n, entry in enumerate(entries):
            if "index" in entry:
                entry["index"] = n  # per-shot indices collide; the lint groups findings by index
        merged = {"frame": frame, list_key: entries, "segments": provenance}
        target.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written[kind] = str(target)
    return written


def stitch(
    segments: list[ShotSegment],
    *,
    ratio: str,
    out_path: Path,
    workdir: Path,
    crossfade_s: float = 0.0,
    transitions: list[tuple[str, float]] | None = None,
    normalize: bool = False,
    video_bitrate: str | None = None,
) -> Path:
    """Concatenate finished per-shot files into one asset.

    By default this uses the ffmpeg CONCAT DEMUXER with ``-c copy`` — the cheapest path, and
    correct when every segment came from the same locked encode settings. A segment already at the
    target ratio's pixel dimensions and not flagged ``reframed`` is returned untouched, so only
    shots that actually need it pay a re-encode cost.

    ``video_bitrate`` (e.g. ``"8M"``) swaps the constant-quality CRF encode for a bitrate target.
    CRF is the better default — it spends bits where the picture needs them — but it is a QUALITY
    target, so a film that is half flat graphics lands at a low average bitrate and trips
    ``video_lint``'s 6 Mbps floor at 1080p even though nothing looks compressed. Set this on a
    final deliverable when that floor has to be met; leave it unset for intermediates.

    ``normalize=True`` re-encodes EVERY segment to those settings first. Use it when the segments
    came from DIFFERENT encoders — a provider's avatar render beside a locally encoded frame
    sequence. Both may report identical width, height, pix_fmt, profile and time_base and still
    carry different SPS/PPS, and an MP4 can hold only one of those: the stream-copy concat then
    produces a file that plays for a few segments and is undecodable after — with no error at all.
    Observed 2026-08-29, where a 2:42 master came out 9:45 at 9.47fps and only failed when
    something tried to decode it.

    This exists so that case has an honest lever. It was reachable before only by setting
    ``reframed=True`` on segments that had not been reframed, which fixes the encode by writing a
    false statement into the manifest.

    When ``crossfade_s > 0`` the join is re-encoded via ``filter_complex`` using ``xfade`` for
    video and ``acrossfade`` for audio. This is intentionally more expensive: crossfades cannot be
    done with stream copy. The requested duration is validated against the adjacent normalized
    segments — if a segment is too short to hold the transition, the function raises rather than
    silently producing a malformed output.

    ``transitions`` is the PER-JOIN form of the same lever, one ``(kind, duration_s)`` per join in
    shot order: ``("cut", 0.0)`` for a hard cut, ``("dissolve", 0.4)`` for xfade's ``fade``,
    ``("fade", 0.5)`` for xfade's ``fadeblack``, ``("push", 0.4)`` for xfade's ``slideup``. It
    exists because a shot list declares the cut INTO each shot (``production.transition_in``) and
    ``crossfade_s`` can only say "every join",
    so honouring a film with one dissolve and one fade to black meant dissolving every cut in it.
    Derive the list with :func:`gtm_core.video_finish.shots.transitions_from_shots`. Passing it
    together with ``crossfade_s`` is refused. An all-cut list keeps the stream-copy path; a list
    with any transition takes the same re-encode ``crossfade_s`` does, with the cut joins made by
    the ``concat`` filter inside that graph. The non-cut joins are recorded beside the master as
    ``<out stem>.transitions.json``.

    Any ``<segment stem>.captions.json`` / ``.overlays.json`` beside a segment is merged onto the
    master's timeline as ``<out stem>.<kind>.json`` — see :func:`_merge_sidecars`; find them
    afterwards with :func:`stitched_sidecars`.
    """
    if not segments:
        raise ValueError("stitch() needs at least one segment")
    joins = _resolve_joins(len(segments), crossfade_s=crossfade_s, transitions=transitions)
    overlaps = [duration for _, duration in joins]
    if ratio not in SAFE_AREAS:
        raise ValueError(f"unknown ratio {ratio!r} — expected one of {sorted(SAFE_AREAS)}")
    area = SAFE_AREAS[ratio]
    workdir.mkdir(parents=True, exist_ok=True)

    # Frame rate is part of "uniform encode settings". The concat demuxer stream-copies, so it
    # cannot reconcile two rates: it emits a non-monotonic-DTS storm and a file whose duration is
    # wrong. Mixed rates are the NORM for this pipeline (a 24fps provider render beside a 30fps
    # local frame sequence), so detect it rather than trusting the caller to notice.
    rates = [_probe_fps(Path(seg.path)) for seg in segments]
    mixed_fps = len({round(r, 3) for r in rates}) > 1
    target_fps = max(rates) if mixed_fps else None
    if mixed_fps and not normalize:
        raise ValueError(
            f"segments carry {len({round(r, 3) for r in rates})} different frame rates "
            f"({', '.join(f'{r:g}' for r in sorted(set(rates)))}fps) — the concat demuxer stream-"
            f"copies and cannot reconcile them, so this would emit a file with the wrong duration "
            f"and no error. Pass normalize=True to re-encode every segment to {max(rates):g}fps."
        )

    normalized = [
        _normalize_shot(
            Path(seg.path),
            target_w=area.width,
            target_h=area.height,
            workdir=workdir / "normalized",
            force_reencode=seg.reframed or normalize,
            video_bitrate=video_bitrate,
            target_fps=target_fps,
        )
        for seg in segments
    ]

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = out_path.with_suffix(out_path.suffix + ".part")

    # Before the encode: a frame mismatch is refused while the fix is still cheap. The transition
    # path below re-derives each offset from the same probed durations.
    _merge_sidecars(
        [Path(seg.path) for seg in segments],
        normalized,
        out_path=out_path,
        overlaps=overlaps,
    )
    _write_transitions_sidecar(segments, joins, out_path=out_path)

    if not any(overlaps):
        # Hard-cut path: concat demuxer + stream copy.
        list_path = workdir / "concat_list.txt"
        list_path.write_text(
            "\n".join(f"file '{p.resolve().as_posix()}'" for p in normalized) + "\n"
        )
        args = [
            ffmpeg_bin,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            "-f",
            "mp4",
            str(part_path),
        ]
        _run_ffmpeg(args)
        os.replace(part_path, out_path)
        return out_path

    # Transition path: filter_complex re-encode.
    durations = [_probe_duration(p) for p in normalized]
    # Fail closed, per segment: a shot must be able to host the longest transition touching it on
    # either side (the 2x is what leaves room when both of its joins are transitions).
    for i, duration in enumerate(durations):
        longest = max(overlaps[max(i - 1, 0) : i + 1])
        if duration < longest * 2:
            asked = (
                f"requested crossfade_s={crossfade_s}s"
                if transitions is None
                else f"the {longest}s transition at join {overlaps.index(longest)}"
            )
            raise ValueError(
                f"{asked} requires each adjacent segment to be at least {longest * 2}s, but "
                f"normalized segment {i} is {duration:.3f}s"
            )

    inputs: list[str] = []
    for p in normalized:
        inputs.extend(["-i", str(p)])

    # Video: chain one filter per join. An xfade starts at the accumulated length of the chain so
    # far minus its own duration; a cut inside this graph is the `concat` filter, which is what
    # keeps a per-join list from having to dissolve the joins the shot list left as cuts.
    #
    # Audio: acrossfade over the same duration, or the audio-only `concat`. acrossfade has no
    # offset param — it always crossfades the overlapping tail/head of its two inputs, so the two
    # tracks stay in step with the video chain join for join.
    #
    # Every input is first pinned to AVTB. `concat` emits AVTB while a demuxed h264 stream carries
    # its own (1/12288 at 24fps), and xfade REFUSES a pair whose timebases differ ("First input
    # link main timebase (1/1000000) do not match ... (1/12288)"): a dissolve AFTER a cut failed to
    # configure and the run wrote no output at all. Pinning up front makes the whole chain agree.
    v_parts = [f"[{i}:v]settb=AVTB[v{i}]" for i in range(len(normalized))]
    a_parts = [f"[{i}:a]asettb=AVTB[a{i}]" for i in range(len(normalized))]
    v_label, a_label = "v0", "a0"
    chain_len = durations[0]
    for i in range(1, len(normalized)):
        kind, duration = joins[i - 1]
        last = i == len(normalized) - 1
        v_out = "vout" if last else f"vf{i}"
        a_out = "aout" if last else f"af{i}"
        if kind == "cut":
            v_parts.append(f"[{v_label}][v{i}]concat=n=2:v=1:a=0[{v_out}]")
            a_parts.append(f"[{a_label}][a{i}]concat=n=2:v=0:a=1[{a_out}]")
        else:
            v_parts.append(
                f"[{v_label}][v{i}]xfade=transition={_XFADE_TRANSITIONS[kind]}:"
                f"duration={duration}:offset={chain_len - duration:.6f}[{v_out}]"
            )
            a_parts.append(f"[{a_label}][a{i}]acrossfade=d={duration}[{a_out}]")
        chain_len += durations[i] - duration
        v_label, a_label = v_out, a_out

    filtergraph = ";".join(v_parts + a_parts)

    args = [
        ffmpeg_bin,
        "-y",
        *inputs,
        "-filter_complex",
        filtergraph,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        # Pinned, not inherited. xfade promotes its output to yuv444p, which libx264 will happily
        # encode — and the resulting file then CANNOT be stream-copy concatenated with any
        # yuv420p segment, so a nested stitch (hard cuts inside groups, dissolves between them)
        # silently produced a master with mangled timestamps rather than an error. yuv420p is also
        # what broad playback requires. `frames_to_video` already pins it; this path had drifted.
        "-pix_fmt",
        "yuv420p",
        "-crf",
        str(DEFAULT_CRF),
        "-preset",
        str(DEFAULT_PRESET),
        "-g",
        str(DEFAULT_GOP),
        "-threads",
        str(DEFAULT_THREADS),
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
