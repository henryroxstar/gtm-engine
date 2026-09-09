from __future__ import annotations

from pathlib import Path

from PIL import Image

from .base import SceneError


def _write_frames(
    frames: list[Image.Image], out_dir: Path, *, prefix: str, alpha: bool = False
) -> int:
    """Write a scene's frames as a numbered PNG sequence.

    ``alpha=True`` keeps the alpha channel instead of flattening to RGB. That is only correct
    for scenes meant to be composited OVER footage (the call UI), never for a full-frame card —
    a card that silently kept an alpha channel would composite onto black and look identical in
    QA while breaking the moment anything was laid under it.
    """
    if not frames:
        raise SceneError("scene produced zero frames")
    out_dir.mkdir(parents=True, exist_ok=True)
    for existing in out_dir.glob(f"{prefix}-*.png"):
        existing.unlink()
    mode = "RGBA" if alpha else "RGB"
    for i, frame in enumerate(frames):
        frame.convert(mode).save(out_dir / f"{prefix}-{i:04d}.png", "PNG")
    return len(frames)


def _frame_count(fps: int, duration_s: float) -> int:
    if fps <= 0:
        raise SceneError(f"fps must be > 0, got {fps}")
    if duration_s <= 0:
        raise SceneError(f"duration_s must be > 0, got {duration_s}")
    return max(2, round(fps * duration_s))
