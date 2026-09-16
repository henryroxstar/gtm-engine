from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ..shots_lint.overlay import normalize_overlay_kind, overlay_entries
from ..video_lint import SAFE_AREAS
from .errors import FfmpegUnavailable
from .ffmpeg import _probe_dims, _probe_duration
from .mux import overlay_frames

# ── overlays — a shot's `production.overlay` bubbles, drawn and composited per shot ────────────
#
# The finish-side half of the chat-bubble path. A shot list says, per shot, which bubble sits
# beside the phone and what it says; `screen_ui`'s `chat-bubble` scene draws it as an RGBA
# sequence; this module sizes that sequence to the shot's own measured window and composites it
# through `mux.overlay_frames`, then writes a sidecar recording where the bubble landed. Per shot,
# before the stitch, for the reason `burn_captions` gives: a window computed inside one shot
# cannot drift out of it, whatever the concat later does to the timeline.


@dataclass(frozen=True)
class OverlaidShot:
    """One shot with its bubbles composited, and the evidence of what landed where."""

    shot_id: str
    src_path: str
    out_path: str
    sidecar_path: str
    duration_s: float
    #: The sidecar's `overlays` rows, verbatim — kind, text, window, measured box, anchor.
    overlays: tuple[dict, ...] = ()


@dataclass(frozen=True)
class OverlayResult:
    ratio: str
    overlaid: tuple[OverlaidShot, ...]
    #: (shot_id, why) for every shot that produced nothing. Always reported, never silent — an
    #: empty out_dir must be explainable.
    skipped: tuple[tuple[str, str], ...]


#: What a `production.overlay` entry may carry. The scene's own keys, the window this module
#: owns, and `note` — free prose the shot list keeps beside the spec for a human reader. Anything
#: else is refused by name: the scene refuses unknown keys, and a key silently dropped here would
#: be a typo that ships the default.
_SCENE_KEYS = ("kind", "text", "side", "y_frac", "w_frac", "arrive_s")
_WINDOW_KEYS = ("start_s", "end_s")
_ALLOWED_KEYS = frozenset((*_SCENE_KEYS, *_WINDOW_KEYS, "note"))

#: Transparent frames appended past the shot's end. `overlay_frames` loops the sequence
#: (`-loop 1`) and cuts at the base's end (`shortest=1`); a sequence exactly as long as the shot
#: can wrap by a frame of rounding and flash the bubble again on the last frame. A second of
#: guard is a few kilobytes of copied transparent PNGs.
_TAIL_GUARD_S = 1.0


def _resolve_window(entry: dict, *, where: str, duration_s: float) -> tuple[float, float]:
    """``(start_s, end_s)`` of the bubble's presence, clamped to the shot's MEASURED duration.

    The declared `duration_s` on the shot is what was asked for; the probe is what exists, and
    the window is cut against the latter for the same reason the caption burn is.
    """
    try:
        start_s = float(entry.get("start_s", 0.0))
        end_s = float(entry.get("end_s", duration_s))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where}: start_s/end_s must be numbers") from exc
    end_s = min(end_s, duration_s)
    if start_s < 0.0 or start_s >= end_s:
        raise ValueError(
            f"{where}: window [{start_s}, {end_s}] is not a forward span inside the shot's "
            f"{duration_s:.3f}s"
        )
    return start_s, end_s


def _padded_sequence(scene_dir: Path, seq_dir: Path, *, count: int, lead: int, total: int) -> str:
    """Lay the scene's frames into a full-shot sequence, transparent before and after.

    The padding frame is the scene's own frame zero, which the scene guarantees fully
    transparent — so this module never draws a pixel and never imports Pillow (the boundary
    `test_captions_module_boundary` keeps). Returns the ffmpeg pattern for the sequence.
    """
    scene = sorted(scene_dir.glob("chat-bubble-*.png"))
    if len(scene) != count:
        raise ValueError(f"expected {count} scene frames in {scene_dir}, found {len(scene)}")
    if seq_dir.exists():
        shutil.rmtree(seq_dir)
    seq_dir.mkdir(parents=True)
    for i in range(total):
        idx = i - lead
        src = scene[idx] if 0 <= idx < count else scene[0]
        shutil.copyfile(src, seq_dir / f"frame-{i:05d}.png")
    return str(seq_dir / "frame-%05d.png")


def overlay_shot(
    shot: dict,
    *,
    kit: dict,
    ratio: str,
    fps: int,
    shots_root: Path,
    out_dir: Path,
    workdir: Path,
    repo_root: Path | None = None,
    shot_id: str | None = None,
) -> OverlaidShot:
    """Draw and composite every `production.overlay` bubble on ONE shot.

    Writes ``<shot_id>-overlaid.mp4`` beside ``<shot_id>-overlaid.overlays.json``, whose shape is
    a contract other readers depend on::

        {"frame": [w, h], "shot_id": "...", "overlays": [
            {"kind": "outgoing", "text": "...", "start_s": 0.25, "end_s": 2.75,
             "box": {"x": .., "y": .., "w": .., "h": ..},
             "anchor": {"side": "right", "y_frac": 0.62, "w_frac": 0.46}}]}

    ``box`` is MEASURED off the scene's own layout (`bubble_geometry`), never derived from the
    anchor: the scene lifts a tall bubble clear of the caption band, and a box computed from
    ``y_frac`` would describe where the bubble was asked to be rather than where it is.
    ``start_s`` is when the bubble begins to appear (the window start plus ``arrive_s``).

    Several entries composite in order, each over the previous result, so "a chip, then a bubble"
    is a two-entry list rather than a compound kind.
    """
    from ..screen_ui.scenes.chat_bubble import (
        bubble_geometry,
        render_chat_bubble_frames,
        resolve_bubble,
    )

    if ratio not in SAFE_AREAS:
        raise ValueError(f"unknown ratio {ratio!r} — expected one of {sorted(SAFE_AREAS)}")
    if fps <= 0:
        raise ValueError(f"fps must be > 0, got {fps}")
    sid = shot_id or str(shot.get("id") or "shot")
    entries = overlay_entries(shot)
    if not entries:
        raise ValueError(f"shot {sid!r} carries no production.overlay")

    file_rel = str(shot.get("file") or "").strip()
    if not file_rel:
        raise ValueError(f"shot {sid!r} has no 'file' field — it has not been rendered yet")
    src = (shots_root / file_rel).resolve()
    if not src.is_file():
        # A refusal, not a skip: a stale path means the shot list and the disk disagree.
        raise ValueError(f"shot {sid!r} names file {file_rel!r} which does not exist at {src}")

    area = SAFE_AREAS[ratio]
    dims = _probe_dims(src)
    if dims != (area.width, area.height):
        # The scene draws at the ratio's native size and the overlay lands at (0, 0) unscaled;
        # a base of another size would put the bubble at the wrong scale and the sidecar's box
        # in a frame that does not exist.
        raise ValueError(
            f"shot {sid!r} is {dims[0]}x{dims[1]} but ratio {ratio} renders at "
            f"{area.width}x{area.height} — reframe the shot first"
        )
    duration_s = _probe_duration(src, stream_selector="v:0")

    out_dir.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{sid}-overlaid.mp4"
    rows: list[dict] = []
    base = src
    for n, entry in enumerate(entries):
        where = f"shot {sid!r} production.overlay[{n}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} is not an object")
        unknown = sorted(set(entry) - _ALLOWED_KEYS)
        if unknown:
            raise ValueError(f"{where} has unknown keys {unknown}")
        kind = normalize_overlay_kind(entry.get("kind"))
        if kind is None:
            raise ValueError(f"{where}.kind {entry.get('kind')!r} is not an overlay kind")
        start_s, end_s = _resolve_window(entry, where=where, duration_s=duration_s)

        bubble = {k: entry[k] for k in _SCENE_KEYS if entry.get(k) not in (None, "")}
        bubble["kind"] = kind
        spec = resolve_bubble(bubble)  # refuses bad copy the same way the render would
        stage = workdir / f"overlay-{sid}-{n}"
        count = render_chat_bubble_frames(
            kit=kit,
            ratio=ratio,
            fps=fps,
            duration_s=end_s - start_s,
            out_dir=stage / "scene",
            bubble=bubble,
            repo_root=repo_root,
        )
        box = bubble_geometry(kit=kit, ratio=ratio, bubble=bubble, repo_root=repo_root)
        lead = round(start_s * fps)
        total = max(lead + count, round((duration_s + _TAIL_GUARD_S) * fps))
        pattern = _padded_sequence(
            stage / "scene", stage / "seq", count=count, lead=lead, total=total
        )
        last = n == len(entries) - 1
        target = out_path if last else workdir / f"overlay-{sid}-{n}.mp4"
        overlay_frames(base, pattern, fps=fps, out_path=target)
        base = target
        rows.append(
            {
                "kind": kind,
                "text": spec["text"],
                "start_s": round(start_s + float(spec["arrive_s"]), 6),
                "end_s": round(end_s, 6),
                "box": box,
                "anchor": {
                    "side": spec["side"],
                    "y_frac": spec["y_frac"],
                    "w_frac": spec["w_frac"],
                },
            }
        )

    sidecar = out_dir / f"{sid}-overlaid.overlays.json"
    sidecar.write_text(
        json.dumps({"frame": [area.width, area.height], "shot_id": sid, "overlays": rows}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return OverlaidShot(
        shot_id=sid,
        src_path=str(src),
        out_path=str(out_path),
        sidecar_path=str(sidecar),
        duration_s=round(duration_s, 6),
        overlays=tuple(rows),
    )


def apply_overlays(
    shots: Sequence[dict],
    *,
    kit: dict,
    ratio: str,
    fps: int,
    shots_root: Path,
    out_dir: Path,
    workdir: Path,
    repo_root: Path | None = None,
    shot_ids: Sequence[str] | None = None,
) -> OverlayResult:
    """:func:`overlay_shot` over a shot list, reporting every shot that produced nothing.

    Same shape as :func:`burn_captions`: a shot with nothing to draw or nothing rendered yet is a
    reported skip; a shot whose file is missing is a refusal, because continuing would produce an
    out_dir that looks like a complete pass.
    """
    if ratio not in SAFE_AREAS:
        raise ValueError(f"unknown ratio {ratio!r} — expected one of {sorted(SAFE_AREAS)}")
    if shutil.which("ffmpeg") is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")

    wanted = set(shot_ids) if shot_ids else None
    if wanted:
        known = {str(sh.get("id") or f"shot-{i:02d}") for i, sh in enumerate(shots)}
        missing = sorted(wanted - known)
        if missing:
            raise ValueError(
                f"--shot-ids names {missing} which are not in this shot list "
                f"(it has {sorted(k for k in known if k)}). A filter that silently matches "
                "nothing looks exactly like a clean run over zero shots."
            )

    overlaid: list[OverlaidShot] = []
    skipped: list[tuple[str, str]] = []
    for i, shot in enumerate(shots):
        shot_id = str(shot.get("id") or f"shot-{i:02d}")
        if wanted is not None and shot_id not in wanted:
            continue
        if not overlay_entries(shot):
            skipped.append((shot_id, "no production.overlay — nothing to draw"))
            continue
        if not str(shot.get("file") or "").strip():
            skipped.append((shot_id, "no 'file' field — this shot has not been rendered yet"))
            continue
        overlaid.append(
            overlay_shot(
                shot,
                kit=kit,
                ratio=ratio,
                fps=fps,
                shots_root=shots_root,
                out_dir=out_dir,
                workdir=workdir,
                repo_root=repo_root,
                shot_id=shot_id,
            )
        )
    return OverlayResult(ratio=ratio, overlaid=tuple(overlaid), skipped=tuple(skipped))


__all__ = ["OverlaidShot", "OverlayResult", "apply_overlays", "overlay_shot"]
