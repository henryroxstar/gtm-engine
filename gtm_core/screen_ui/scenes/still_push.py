from __future__ import annotations

from pathlib import Path

from PIL import Image

from ..base import SceneError
from ..draw import _ease_in_out, _lerp
from ..frames import _frame_count, _write_frames
from ..palette import _load_palette, _resolve_area

# ── still-push — animate an asset the profile ALREADY HOLDS ────────────────────────────────────


def render_still_push_frames(
    *,
    kit: dict,
    ratio: str,
    fps: int,
    duration_s: float,
    out_dir: Path,
    image: Path | None = None,
    crop_frac: tuple[float, float, float, float] | None = None,
    font_role: str = "caption",
    repo_root: Path | None = None,
) -> int:
    """A slow push across an existing image, as a frame sequence.

    This exists so "reuse > generate" is actually BUILDABLE. The preflight lists every asset a
    profile already holds and calls reuse its highest-leverage move, but a real product screenshot
    had no path to becoming a shot that moves — so it either shipped as a dead still (the V9
    static-shot defect) or got replaced by a generated abstract, which is the outcome the rule
    exists to prevent.

    Cover-fits the source to the delivery ratio and eases from 1.00 to 1.06 scale. No text is
    drawn: whatever the asset says, it says itself.
    """
    area = _resolve_area(ratio)
    if image is None:
        raise SceneError("still-push needs --image: the asset to animate")
    src_path = Path(image)
    if not src_path.is_file():
        raise SceneError(f"--image not found: {src_path}")
    _ = _load_palette(kit)  # validates the kit the same way every other scene does

    with Image.open(src_path) as im:
        src = im.convert("RGB").copy()

    if crop_frac is not None:
        # A REGION, chosen before any scaling. A full application window pasted in whole is the
        # 2026-08-29 console shot: 3416px of UI cover-fitted into 1920, so every table row landed
        # around 8px and nothing in it could be read at any size ("clearer?", 2026-08-30). A
        # screenshot earns its place by carrying ONE legible fact, which means cropping to the
        # part that carries it and letting the push do the rest.
        left, top, right, bottom = crop_frac
        if not (0.0 <= left < right <= 1.0 and 0.0 <= top < bottom <= 1.0):
            raise SceneError(
                f"crop_frac must be 0<=left<right<=1 and 0<=top<bottom<=1, got {crop_frac}"
            )
        src = src.crop(
            (
                round(src.width * left),
                round(src.height * top),
                round(src.width * right),
                round(src.height * bottom),
            )
        )

    w, h = area.width, area.height
    n_frames = _frame_count(fps, duration_s)
    START_SCALE, END_SCALE = 1.00, 1.06

    frames: list[Image.Image] = []
    for i in range(n_frames):
        t = i / (n_frames - 1) if n_frames > 1 else 1.0
        scale = _lerp(START_SCALE, END_SCALE, _ease_in_out(t))
        # Cover-fit: the crop window is the largest region of the SOURCE with the delivery ratio,
        # shrunk by `scale` so a bigger scale means a tighter crop, i.e. a push in.
        src_ratio, out_ratio = src.width / src.height, w / h
        if src_ratio > out_ratio:
            ch = src.height / scale
            cw = ch * out_ratio
        else:
            cw = src.width / scale
            ch = cw / out_ratio
        cx, cy = src.width / 2, src.height / 2
        box = (round(cx - cw / 2), round(cy - ch / 2), round(cx + cw / 2), round(cy + ch / 2))
        frames.append(src.resize((w, h), Image.LANCZOS, box=box))

    return _write_frames(frames, out_dir, prefix="still-push")
