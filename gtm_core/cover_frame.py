"""Deterministic cover-frame CANDIDATE selection (Phase E).

Extracts N evenly-spaced candidate frames via ffmpeg, scores each by Laplacian-variance
(a classic focus/sharpness metric — a blurry motion-blur frame scores low, a crisp held frame
scores high), and returns the highest-scoring one as the cover frame.

This is explicitly a DETERMINISTIC CANDIDATE, never a claim of AI-grade thumbnail selection —
Higgsfield's ``youtube-thumbnail-generator`` is the real upgrade path. This module is
the free, always-available fallback :mod:`gtm_core.video_finish` reaches for when that isn't run,
not a replacement for it.

SECOND OF THREE MODULES IN gtm_core/ PERMITTED TO IMPORT PILLOW
------------------------------------------------------------------------
:mod:`gtm_core.captions` already carries this exception for text rasterization;
:mod:`gtm_core.screen_ui` (the intercut lane, P4) is the third, for drawing whole UI-mockup frame
sequences from scratch. ``tests/media/test_captions_module_boundary.py`` enforces the exact
three-module allowlist. Pixel-level image work stays confined to these named, reviewed modules —
never scattered across the ffmpeg-orchestration layer (:mod:`gtm_core.video_finish`), the same
discipline that kept ``drawtext`` out of captions.py in the first place (F1).
"""

from __future__ import annotations

import json
import shutil
import statistics
import subprocess  # nosec B404 — ffmpeg/ffprobe orchestration, arg lists only, never shell=True
import tempfile
from pathlib import Path

from PIL import Image, ImageFilter

#: The classic 3x3 discrete Laplacian kernel — approximates the second derivative of pixel
#: intensity, so its variance over an image is high where edges are sharp/in-focus and low where
#: the image is smooth/blurred. Applied to a GRAYSCALE conversion; color carries no focus signal.
_LAPLACIAN_KERNEL = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1)

DEFAULT_CANDIDATE_COUNT = 5


class FfmpegUnavailable(RuntimeError):
    """ffmpeg/ffprobe is not on PATH."""


def _ffprobe_duration(src: Path) -> float:
    ffprobe_bin = shutil.which("ffprobe")
    if ffprobe_bin is None:
        raise FfmpegUnavailable("ffprobe is not on PATH")
    out = subprocess.run(  # nosec B603 — arg list resolved via shutil.which, never shell=True
        [
            ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(src),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


def extract_candidates(
    src: Path, *, out_dir: Path, count: int = DEFAULT_CANDIDATE_COUNT
) -> list[Path]:
    """Extract ``count`` evenly-spaced frames from ``src`` as PNGs under ``out_dir``. Skips the
    very first and last ~5% of the timeline (fade-in/fade-out/black-frame territory) — the
    candidate window is the middle 90% of the asset."""
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")
    if count < 1:
        raise ValueError(f"count must be >= 1, got {count}")

    duration = _ffprobe_duration(src)
    margin = duration * 0.05
    window = max(duration - 2 * margin, 0.0)
    out_dir.mkdir(parents=True, exist_ok=True)

    candidates: list[Path] = []
    for i in range(count):
        t = margin + (window * i / max(count - 1, 1)) if count > 1 else duration / 2
        dst = out_dir / f"candidate_{i:02d}.png"
        subprocess.run(  # nosec B603 — arg list resolved via shutil.which, never shell=True
            [
                ffmpeg_bin,
                "-y",
                "-ss",
                f"{t:.3f}",
                "-i",
                str(src),
                "-frames:v",
                "1",
                str(dst),
            ],
            check=True,
            capture_output=True,
        )
        candidates.append(dst)
    return candidates


def laplacian_variance(png_path: Path) -> float:
    """Sharpness score for one frame — higher is sharper/more in-focus."""
    img = Image.open(png_path).convert("L")
    edges = img.filter(_LAPLACIAN_KERNEL)
    return statistics.pvariance(edges.getdata())


def pick_cover_frame(
    src: Path, *, out_path: Path, workdir: Path, count: int = DEFAULT_CANDIDATE_COUNT
) -> Path:
    """Extract ``count`` candidates and copy the sharpest (highest Laplacian-variance) one to
    ``out_path``. Raises FfmpegUnavailable if ffmpeg/ffprobe is absent."""
    candidates = extract_candidates(src, out_dir=workdir / "cover_candidates", count=count)
    best = max(candidates, key=laplacian_variance)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(best, out_path)
    return out_path


def contact_sheet(
    src: Path, dst: Path, *, count: int = 8, tile_w: int = 320, quality: int = 82
) -> dict:
    """Tile ``count`` evenly-spaced frames of ``src`` into one horizontal contact sheet.

    A QA artifact, not a delivery format: it exists so a finished asset can be *looked at* before
    it ships. That check had no sanctioned path before — every pixel operation in this pipeline is
    confined to this module and :mod:`gtm_core.captions`, so "just run ffmpeg to grab a frame" was
    the one QA step the architecture pushed outside itself. On 2026-08-18 the operator first saw a
    frame after the render budget was spent and rejected the result on sight; on 2026-08-19 a
    sheet caught three defects (captions chunked across shot boundaries, the Article 50 disclosure
    drawn on top of a caption, and a b-roll shot whose palette had drifted off-brand) that the
    linter passed with zero findings, because none of them is measurable from geometry alone.

    Lives here rather than in :mod:`gtm_core.video_finish` for the reason in this module's header:
    the ffmpeg-orchestration layer does not import Pillow.
    """
    if count < 1:
        raise ValueError(f"count must be >= 1, got {count}")
    with tempfile.TemporaryDirectory() as tmp:
        frames = extract_candidates(src, out_dir=Path(tmp), count=count)
        images = [Image.open(f).convert("RGB") for f in frames]
        tile_h = round(tile_w * images[0].height / images[0].width)
        sheet = Image.new("RGB", (tile_w * len(images), tile_h), (10, 12, 20))
        for i, im in enumerate(images):
            sheet.paste(im.resize((tile_w, tile_h), Image.LANCZOS), (i * tile_w, 0))
        dst.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(dst, quality=quality)
    return {"out_path": str(dst), "frames": len(frames)}


def sibling_sheet(
    paths: list[Path], dst: Path, *, columns: int = 4, tile_w: int = 320, quality: int = 82
) -> dict:
    """Tile N SEPARATE stills into a grid, so a human can pick one by looking.

    The sibling of :func:`contact_sheet`, and deliberately a different function rather than a flag
    on it: that one samples frames *along one video's timeline* and is a QA artifact; this one
    lays out *unrelated candidates side by side* and is a CHOOSING artifact. Same pixels, opposite
    questions — "did this ship correctly" versus "which of these do I want".

    Two callers: an element's pose sheet (C11), and a render pool's alternates (C5). Lives here
    for this module's standing reason — the ffmpeg-orchestration layer does not import Pillow, and
    ``tests/media/test_captions_module_boundary.py`` pins which modules may.

    Tiles are letterboxed into a uniform cell rather than stretched: a 9:16 pose and a 1:1 pose in
    one sheet is the normal case, and distorting either to fill the grid would misrepresent the
    thing being chosen.
    """
    if not paths:
        raise ValueError("sibling_sheet needs at least one image")
    if columns < 1:
        raise ValueError(f"columns must be >= 1, got {columns}")

    # Two passes on purpose. `Image.open(...).size` reads the header only, so the cell height is
    # known without decoding a pixel; each still is then decoded, resized and dropped in turn.
    # The first version decoded every still up front and held all of them — at the documented
    # pose size (1536x2752) a sixteen-pose sheet was ~200 MB resident to write one JPEG.
    sizes = []
    for p in paths:
        with Image.open(p) as im:
            sizes.append(im.size)
    cell_h = max(round(tile_w * h / w) for w, h in sizes)
    rows = (len(paths) + columns - 1) // columns
    sheet = Image.new("RGB", (tile_w * columns, cell_h * rows), (10, 12, 20))

    for i, (p, (iw, ih)) in enumerate(zip(paths, sizes, strict=True)):
        scale = min(tile_w / iw, cell_h / ih)
        w, h = max(1, round(iw * scale)), max(1, round(ih * scale))
        cx, cy = (i % columns) * tile_w, (i // columns) * cell_h
        with Image.open(p) as im:
            tile = im.convert("RGB").resize((w, h), Image.LANCZOS)
        sheet.paste(tile, (cx + (tile_w - w) // 2, cy + (cell_h - h) // 2))

    dst.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dst, quality=quality)
    return {"out_path": str(dst), "tiles": len(paths), "columns": columns, "rows": rows}


def main(argv: list[str] | None = None) -> int:
    """`python -m gtm_core.cover_frame sibling-sheet --paths A B C --out sheet.jpg`.

    A CLI because skills are markdown executed by the brain: they cannot import Python, and the
    least-privilege policy allows `python -m …` but not `python -c …`. Without this, the contact
    sheet `video-render` asks for is a step no body can actually take.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="uv run python -m gtm_core.cover_frame")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sheet = sub.add_parser(
        "sibling-sheet", help="tile N separate stills or clips into one grid to choose from"
    )
    p_sheet.add_argument("--paths", nargs="+", required=True, type=Path)
    p_sheet.add_argument("--out", required=True, type=Path)
    p_sheet.add_argument("--columns", type=int, default=4)
    p_sheet.add_argument("--tile-w", type=int, default=320)

    p_contact = sub.add_parser(
        "contact-sheet", help="tile evenly-spaced frames of ONE video, for QA"
    )
    p_contact.add_argument("--src", required=True, type=Path)
    p_contact.add_argument("--out", required=True, type=Path)
    p_contact.add_argument("--count", type=int, default=8)

    args = parser.parse_args(argv)

    from .confine import ConfinementError, confined_output_path
    from .paths import resolve_content_root

    try:
        # A sheet is a tenant artifact like any other: it lands inside the content root or not
        # at all. (The Python functions stay unconfined for in-process callers with their own
        # root, e.g. the elements CLI; the SHELL surface is where an arbitrary path arrives.)
        out = confined_output_path(args.out, content_root=resolve_content_root())
        if args.cmd == "contact-sheet":
            result = contact_sheet(args.src, out, count=args.count)
        else:
            # A pool member may be a clip rather than a still — tile its MIDPOINT frame. Not the
            # first: every member of a render pool was animated from the SAME start still, so
            # first frames are identical and cannot tell the takes apart; the midpoint is where
            # they have diverged. (The comment used to say "first", contradicting the code — the
            # code was right.) Per-file scratch dirs let a mixed pool of stills and clips work
            # without the caller sorting them first.
            with tempfile.TemporaryDirectory() as tmp:
                tiles: list[Path] = []
                for i, path in enumerate(args.paths):
                    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                        tiles.append(path)
                        continue
                    frames = extract_candidates(path, out_dir=Path(tmp) / f"m{i}", count=1)
                    if not frames:
                        raise ValueError(f"no frame could be extracted from {path}")
                    tiles.append(frames[0])
                result = sibling_sheet(tiles, out, columns=args.columns, tile_w=args.tile_w)
    except (
        OSError,
        ValueError,
        RuntimeError,
        subprocess.CalledProcessError,
        ConfinementError,
    ) as exc:
        print(f"[cover-frame] {exc}")
        return 2

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
