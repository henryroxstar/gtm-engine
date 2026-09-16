"""``fit-layout`` — measure a scene's real content box, solve the largest size that clears the
caption band, and report both. PIL is allowed in this module (same allowlist as ``captions.py``
and the rest of ``screen_ui/`` — see ``tests/media/test_captions_module_boundary.py``).

Two beats produced this: a phone-walkthrough re-render that took two manual sizing rounds
because nothing could answer "how much room is actually free" before a render was spent finding
out, and a caption-band formula that was never checked against a real rendered caption (see
:func:`caption_band`'s docstring).

**Caption band — lives here, not in ``captions.py``.** ``captions.py`` is already at its §R10
ceiling (1081 lines, ``tests/lint/complexity_allowlist.txt``), and this is one of the only modules
allowed to import PIL, so a helper needing PIL belongs here regardless. :func:`caption_band` calls
``captions.py``'s own PUBLIC ``fit()``, ``_safe_box_px``, ``_placement_height_budget``,
``_block_top`` and ``resolve_placement`` — importing the private helpers is fine (same package
tree, the precedent ``hero_reveal``/``phone_chassis`` already set across scene modules) — rather
than reimplementing the block-height math a second time.

**Hero content box** diffs a still against its own sampled background colour
(``hero_reveal._canvas_rgb``, shared by construction — this module never derives its own idea of
"the background"), unions in a highlight's ``dim_rect`` and ring extent, and maps the result
through hero-reveal's own non-uniform x/y scale (the layer resize does NOT preserve aspect ratio).

**Phone box** reads ``phone_chassis.phone_geometry``/``.origin`` and the real ``shadow()`` mask's
own canvas size (an exact upper bound on the shadow's visible footprint — GaussianBlur cannot
paint outside the canvas it is blurred into, so no separate "how far does a blur spread" guess is
needed there). A ``zoom`` action is measured via ``phone_walkthrough._zoom_pose`` at its held
(fully eased-in) pose, not exempted — an explicit ``scale`` can push the zoomed rect into the
caption band exactly as easily as a bad static layout can.

**Centring.** Both scenes place their box via an affine ``center_*_frac`` (shifting it shifts the
whole box uniformly — ``hero_reveal``'s ``base_y = cy_frac*h - sh/2`` and ``phone_chassis``'s
``origin`` are both linear in the centring fraction), EXCEPT a phone's zoomed rect at full zoom,
which is pinned to a fixed frame point (``phone_walkthrough._ZOOM_FOCUS``) and does not move with
``center_y_frac`` at all. So the solver centres the REST box (chassis + shadow, or hero's content
box) in the free band by a closed-form shift, then re-measures the FULL box — zoom included — at
that centring: if the zoomed rect alone pushes past a margin even there, that scale/phone_h_frac
genuinely does not fit and is correctly rejected, never silently accepted.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageChops, ImageFont

from ..captions import (
    BRAND_MAX_PX,
    LINE_SPACING,
    FontMissing,
    _block_top,
    _placement_height_budget,
    _safe_box_px,
    load_face,
    resolve_placement,
)
from ..captions import fit as _caption_fit
from ..design_tokens import stroke_px
from ..video_lint import SAFE_AREAS
from .scenes import phone_marks as marks
from .scenes.hero_reveal import _SCALE_RANGE as _HERO_SCALE_RANGE
from .scenes.hero_reveal import _Action as _HeroAction
from .scenes.hero_reveal import _canvas_rgb
from .scenes.hero_reveal import _load_actions as _hero_load_actions
from .scenes.phone_actions import Action as _WalkAction
from .scenes.phone_actions import fit_screen, parse_actions
from .scenes.phone_chassis import (
    _CENTER_Y_FRAC_RANGE,
    _PHONE_H_FRAC_RANGE,
    PhoneLayout,
    phone_geometry,
    placed,
    shadow,
)
from .scenes.phone_chassis import parse_layout as _parse_phone_layout
from .scenes.phone_walkthrough import _zoom_pose

#: Content-diff tolerance against the sampled background colour — a JPEG-noise or antialiasing
#: fringe within this Manhattan distance still counts as background, not content.
_CONTENT_TOLERANCE = 12
#: Scale/phone_h_frac step the solver rounds DOWN to — a layout value a human will paste verbatim,
#: not a float with noise past the second decimal.
_SOLVE_STEP = 0.01


class FitLayoutError(ValueError):
    """``fit-layout`` cannot answer the question as asked (bad flags, unreadable image, no font)."""


Box = tuple[float, float, float, float]  # (x0, y0, x1, y1) in absolute frame pixels


# ── caption band ────────────────────────────────────────────────────────────────────────────────


def caption_band(
    kit: dict, *, ratio: str, lines: int, text: str | None = None, repo_root: Path | None = None
) -> tuple[float, float]:
    """``(top, bottom)`` — the absolute frame-pixel band a burned caption can occupy, including
    its scrim and blur footprint, for ``lines`` lines (or the real wrap of ``text`` when given).

    Uses ``fit()``'s own returned ``font_px`` when ``text`` is known; otherwise uses
    :data:`gtm_core.captions.BRAND_MAX_PX` (62) as the worst-case upper bound — ``fit()`` never
    returns a size ABOVE it, only at or below, so sizing the band for 62px is always at least as
    wide as whatever a real caption will draw.

    **Blur extent is ``3 * round(px * 0.18)``, not ``round(px * 0.18)``.** ``captions.render()``
    blurs its scrim with ``ImageFilter.GaussianBlur(round(font_px * 0.18))`` — that argument is the
    blur's SIGMA, and a Gaussian's visible alpha runs to roughly ±3σ, not ±σ. Measured against a
    real rendered caption's alpha channel (``tests/media/test_screen_ui_fit_layout.py``): the naive
    ``round(px*0.18)`` undersizes the true footprint by roughly 2.5x, which is exactly the kind of
    error that fails silently — the band looks reserved and isn't. The 3x formula is asserted
    against a MEASURED bbox there, not trusted as algebra.

    Not the same band as ``screen_ui/draw.py``'s ``CAPTION_BAND_TOP_FRAC`` (0.70) — that one is a
    Reap-calibrated constant for the PROVIDER burn path four scenes share; this one is the LOCAL
    Pillow burn path's own geometry. Two different bands for two different caption paths, not
    meant to agree.
    """
    if ratio not in SAFE_AREAS:
        raise FitLayoutError(f"unknown ratio {ratio!r} — expected one of {sorted(SAFE_AREAS)}")
    area = SAFE_AREAS[ratio]
    placement = resolve_placement(kit, ratio=ratio)
    face = load_face(kit, repo_root=repo_root)
    _box_x, box_y, _box_w, box_h = _safe_box_px(area)
    max_height_px = _placement_height_budget(area, placement)

    if text is not None:
        wrapped, font_px = _caption_fit(text, area=area, face=face, max_height_px=max_height_px)
        n_lines = len(wrapped)
    else:
        font_px = BRAND_MAX_PX
        n_lines = lines

    font = ImageFont.truetype(str(face), font_px)
    ascent, descent = font.getmetrics()
    line_height = (ascent + descent) * LINE_SPACING
    block_height = n_lines * line_height
    if max_height_px is not None:
        block_height = min(block_height, max_height_px)
    block_top = _block_top(box_y, box_h, block_height, placement)

    pad_y = round(font_px * 0.34)
    blur_extent = 3 * round(font_px * 0.18)
    margin = pad_y + blur_extent
    top = max(0.0, block_top - margin)
    bottom = min(float(area.height), block_top + block_height + margin)
    return top, bottom


# ── hero-reveal content box ────────────────────────────────────────────────────────────────────


def _load_still(path: Path) -> Image.Image:
    src = Path(path)
    if not src.is_file():
        raise FitLayoutError(f"--image not found: {src}")
    try:
        with Image.open(src) as im:
            return im.convert("RGB").copy()
    except (OSError, Image.DecompressionBombError) as exc:
        raise FitLayoutError(f"--image {src} could not be read: {exc}") from exc


def _content_bbox(src: Image.Image, bg: tuple[int, int, int]) -> tuple[int, int, int, int]:
    """The bbox of every pixel differing from ``bg`` by more than :data:`_CONTENT_TOLERANCE`, in
    ANY channel (not a luma-weighted diff, which underestimates a pure-hue difference) — or the
    whole image when nothing differs (a flat still is still a still, not a crash)."""
    canvas = Image.new("RGB", src.size, bg)
    diff = ImageChops.difference(src, canvas)
    r, g, b = diff.split()
    combined = ImageChops.lighter(ImageChops.lighter(r, g), b)
    mask = combined.point(lambda p: 255 if p > _CONTENT_TOLERANCE else 0)
    return mask.getbbox() or (0, 0, src.width, src.height)


def _ring_extent_src(
    rect: tuple[float, float, float, float], shape: str, content_w: int, content_h: int
) -> Box:
    """The ring's own outer box in SOURCE pixel space — ``marks.ring_box``'s pad plus the full
    stroke width, so the extent is a safe outer bound rather than the stroke's centreline."""
    pad = content_w * 0.022
    box = marks.ring_box(rect, shape, pad)
    stroke = stroke_px("emphasis", content_h)
    return box[0] - stroke, box[1] - stroke, box[2] + stroke, box[3] + stroke


def _hero_box_src(image: Path, actions: dict | None, duration_s: float):
    """``(box_src, src_size, layout)`` — the union of content bbox, every highlight's
    ``dim_rect``, and every highlight's ring extent, in SOURCE pixel space, clamped to the source
    image's own bounds. The clamp stands in for an idle-motion margin: idle drift is a crop
    INSIDE the already-sized layer (``hero_reveal.py``), never a translation of the layer's own
    outer edges, so a source-space box already inside the source image needs no further allowance.
    """
    src = _load_still(image)
    bg = _canvas_rgb(src)
    x0, y0, x1, y1 = _content_bbox(src, bg)
    layout = None
    if actions is not None:
        plan, layout = _hero_load_actions(actions, duration_s=duration_s)
        for a in plan:
            if not isinstance(a, _HeroAction) or a.kind != "highlight":
                continue
            if a.dim_rect is not None:
                dx0, dy0, dx1, dy1 = a.dim_rect
                x0, y0, x1, y1 = min(x0, dx0), min(y0, dy0), max(x1, dx1), max(y1, dy1)
            if a.rect is not None:
                rx0, ry0, rx1, ry1 = _ring_extent_src(a.rect, a.shape, src.width, src.height)
                x0, y0, x1, y1 = min(x0, rx0), min(y0, ry0), max(x1, rx1), max(y1, ry1)
    x0, y0 = max(0.0, x0), max(0.0, y0)
    x1, y1 = min(float(src.width), x1), min(float(src.height), y1)
    return (x0, y0, x1, y1), src.size, layout


def _hero_box_frame(
    box_src: Box, src_size: tuple[int, int], frame_size: tuple[int, int], *,
    scale: float, cx_frac: float, cy_frac: float,
) -> Box:  # fmt: skip
    """Map a source-space box into frame pixels via hero-reveal's own placement arithmetic
    (``hero_reveal.py``'s ``_frame``) — non-uniform x/y scale, since the layer resize to
    ``(sw, sh)`` does not preserve the source's aspect ratio."""
    sw_src, sh_src = src_size
    w, h = frame_size
    sw = max(1, round(w * scale))
    sh = max(1, round(h * scale))
    x_scale, y_scale = sw / sw_src, sh / sh_src
    base_x = cx_frac * w - sw / 2
    base_y = cy_frac * h - sh / 2
    x0, y0, x1, y1 = box_src
    return (
        base_x + x0 * x_scale,
        base_y + y0 * y_scale,
        base_x + x1 * x_scale,
        base_y + y1 * y_scale,
    )


# ── phone-walkthrough box ──────────────────────────────────────────────────────────────────────


class _FitOnlyScreen:
    """A stand-in for ``phone_walkthrough._Screen`` carrying only what ``_zoom_pose`` reads
    (``.fit``) — geometry needs no pixels, and loading N real images for N screen indices this
    CLI never received (it exposes only one ``--image``) would be both wasted work and, for
    indices beyond 0, impossible."""

    def __init__(self, fit: object) -> None:
        self.fit = fit


def _phone_box_frame(
    image_size: tuple[int, int], actions: dict | None, duration_s: float,
    frame_size: tuple[int, int], *, phone_h_frac: float, center_y_frac: float,
) -> Box:  # fmt: skip
    """The phone chassis's own rect, unioned with its shadow's exact canvas bound and, when
    ``actions`` carries a zoom, the zoomed rect at its fully-eased-in (held) pose — checked, not
    exempted, since an explicit ``scale`` can push the zoomed rect into the caption band exactly
    as easily as a bad static layout.

    A ``swap`` target beyond screen 0 is measured against screen 0's OWN aspect (the only real
    image this CLI has) — a layout-geometry check, not a content check, and every screen a real
    walkthrough uses is the same phone-shaped capture.
    """
    w, h = frame_size
    layout = PhoneLayout(phone_h_frac=phone_h_frac, center_y_frac=center_y_frac)
    geom = phone_geometry(w, h, layout=layout)
    ox, oy = geom.origin
    p = placed(geom, 1.0)
    x0, y0, x1, y1 = ox, oy, ox + p.phone_w, oy + p.phone_h
    mask, sdx, sdy = shadow(p)
    sx0, sy0 = round(ox) + sdx, round(oy) + sdy
    sx1, sy1 = sx0 + mask.width, sy0 + mask.height
    x0, y0, x1, y1 = min(x0, sx0), min(y0, sy0), max(x1, sx1), max(y1, sy1)

    if actions is None:
        return x0, y0, x1, y1
    fit0 = fit_screen(*image_size)
    n_screens = 1 + max((a.get("screen", 0) for a in actions.get("actions", [])), default=0)
    zoom_screens = [_FitOnlyScreen(fit0) for _ in range(max(1, n_screens))]
    plan = parse_actions(actions, screens=[image_size] * len(zoom_screens), duration_s=duration_s)
    for a in plan:
        if not isinstance(a, _WalkAction) or a.kind != "zoom":
            continue
        t_full = a.start_s + a.ease_s  # the held, fully eased-in moment — worst case
        k, zx, zy = _zoom_pose(a, t_full, geom, zoom_screens, plan)
        zx1, zy1 = zx + k * p.phone_w, zy + k * p.phone_h
        x0, y0, x1, y1 = min(x0, zx), min(y0, zy), max(x1, zx1), max(y1, zy1)
    return x0, y0, x1, y1


# ── solve ───────────────────────────────────────────────────────────────────────────────────────


def _free_band(
    placement: str, *, h: float, band_top: float, band_bottom: float, margin: float
) -> tuple[float, float]:
    if placement == "lower":
        return margin, band_top - margin
    return band_bottom + margin, h - margin


def _fits(box: Box, *, w: float, margin: float, free_top: float, free_bottom: float) -> bool:
    x0, y0, x1, y1 = box
    return x0 >= margin and x1 <= w - margin and y0 >= free_top and y1 <= free_bottom


def _clearance(box: Box, *, placement: str, free_top: float, free_bottom: float) -> float:
    return (free_bottom - box[3]) if placement == "lower" else (box[1] - free_top)


def _centred_cy_frac(
    base_box: Box, *, frame_h: float, free_top: float, free_bottom: float
) -> float:
    """The ``center_*_frac`` shift that puts ``base_box`` (measured at ``cy_frac=0.5``, the REST
    geometry only) centred in the free band — exact, since both scenes' centring is affine."""
    free_mid = (free_top + free_bottom) / 2
    base_mid = (base_box[1] + base_box[3]) / 2
    return 0.5 + (free_mid - base_mid) / frame_h


def _solve(
    measure: Callable[[float], tuple[Box, float]], *, lo: float, hi: float, exclusive_lo: bool,
    w: float, margin: float, free_top: float, free_bottom: float,
) -> float | None:  # fmt: skip
    """The largest value in ``[lo, hi]``, rounded DOWN to :data:`_SOLVE_STEP`, at which
    ``measure(value)``'s box fits. ``None`` when nothing in range fits."""
    start = lo + _SOLVE_STEP if exclusive_lo else lo
    steps = int(round((hi - start) / _SOLVE_STEP))
    best: float | None = None
    for i in range(steps + 1):
        candidate = round(start + i * _SOLVE_STEP, 2)
        box, _cy = measure(candidate)
        if _fits(box, w=w, margin=margin, free_top=free_top, free_bottom=free_bottom):
            best = candidate
    return best


# ── report ──────────────────────────────────────────────────────────────────────────────────────


def _read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def report(
    *, for_scene: str, kit: dict, ratio: str, image: Path | None = None,
    actions: Path | dict | None = None, caption_lines: int = 2, margin_px: int = 40,
    repo_root: Path | None = None,
) -> dict:  # fmt: skip
    """Measure, solve, and report — the full ``fit-layout`` payload. Raises
    :class:`FitLayoutError` for a bad ``--for-scene``, a missing image, or a kit with no
    resolvable font (the caption band cannot be computed without one). Exit 0 fits, 1 the current
    layout clips or overlaps, 2 usage/refusal (decided by :func:`run_cli` from ``doc["ok"]``).
    """
    if for_scene not in ("hero-reveal", "phone-walkthrough"):
        raise FitLayoutError(
            f"--for-scene must be hero-reveal or phone-walkthrough, got {for_scene!r}"
        )
    if ratio not in SAFE_AREAS:
        raise FitLayoutError(f"unknown ratio {ratio!r} — expected one of {sorted(SAFE_AREAS)}")
    if image is None:
        raise FitLayoutError("--image is required")
    try:
        load_face(kit, repo_root=repo_root)
    except FontMissing as exc:
        raise FitLayoutError(str(exc)) from exc

    area = SAFE_AREAS[ratio]
    w, h = float(area.width), float(area.height)
    placement = resolve_placement(kit, ratio=ratio)
    band_top, band_bottom = caption_band(kit, ratio=ratio, lines=caption_lines, repo_root=repo_root)
    free_top, free_bottom = _free_band(
        placement, h=h, band_top=band_top, band_bottom=band_bottom, margin=margin_px
    )

    doc_actions = actions if actions is None or isinstance(actions, dict) else _read_json(actions)
    # Geometry only — no frame is drawn, so the window just has to cover every action's end_s.
    # Exactly `max(ends)`, not padded: phone-walkthrough's own `parse_actions` refuses a gap of
    # more than `MAX_IDLE_S` between the last action and `duration_s` (`_check_idle`), so an
    # arbitrary "big enough" duration_s would turn a normal actions file into a spurious refusal.
    duration_s = 1.0
    if doc_actions is not None:
        ends = [a.get("end_s", 0.0) for a in doc_actions.get("actions", [])]
        if ends:
            duration_s = max(ends)

    content_box_src: list[float] | None = None
    if for_scene == "hero-reveal":
        box_src, src_size, current_layout = _hero_box_src(image, doc_actions, duration_s)
        content_box_src = [round(v, 1) for v in box_src]
        cx0 = current_layout.center_x_frac if current_layout else 0.5
        cy0 = current_layout.center_y_frac if current_layout else 0.5
        scale0 = current_layout.scale if current_layout else 1.0
        current_box = _hero_box_frame(box_src, src_size, (w, h), scale=scale0, cx_frac=cx0, cy_frac=cy0)  # fmt: skip

        def measure(scale: float) -> tuple[Box, float]:
            base = _hero_box_frame(box_src, src_size, (w, h), scale=scale, cx_frac=0.5, cy_frac=0.5)
            cy = _centred_cy_frac(base, frame_h=h, free_top=free_top, free_bottom=free_bottom)
            return _hero_box_frame(
                box_src, src_size, (w, h), scale=scale, cx_frac=0.5, cy_frac=cy
            ), cy

        lo, hi = _HERO_SCALE_RANGE
        solved = _solve(measure, lo=lo, hi=hi, exclusive_lo=True, w=w, margin=margin_px, free_top=free_top, free_bottom=free_bottom)  # fmt: skip
    else:
        img = _load_still(image)
        raw_layout = doc_actions.get("layout") if isinstance(doc_actions, dict) else None
        current_layout = _parse_phone_layout(raw_layout)
        current_box = _phone_box_frame(
            img.size, doc_actions, duration_s, (w, h),
            phone_h_frac=current_layout.phone_h_frac, center_y_frac=current_layout.center_y_frac,
        )  # fmt: skip

        def measure(phone_h_frac: float) -> tuple[Box, float]:
            base = _phone_box_frame(img.size, None, duration_s, (w, h), phone_h_frac=phone_h_frac, center_y_frac=0.5)  # fmt: skip
            cy = _centred_cy_frac(base, frame_h=h, free_top=free_top, free_bottom=free_bottom)
            cy = max(_CENTER_Y_FRAC_RANGE[0], min(_CENTER_Y_FRAC_RANGE[1], cy))
            box = _phone_box_frame(img.size, doc_actions, duration_s, (w, h), phone_h_frac=phone_h_frac, center_y_frac=cy)  # fmt: skip
            return box, cy

        lo, hi = _PHONE_H_FRAC_RANGE
        solved = _solve(measure, lo=lo, hi=hi, exclusive_lo=False, w=w, margin=margin_px, free_top=free_top, free_bottom=free_bottom)  # fmt: skip

    current_ok = _fits(
        current_box, w=w, margin=margin_px, free_top=free_top, free_bottom=free_bottom
    )
    current_clearance = _clearance(
        current_box, placement=placement, free_top=free_top, free_bottom=free_bottom
    )

    recommended: dict | None = None
    if solved is not None:
        rec_box, rec_cy = measure(solved)
        rec_clearance = _clearance(
            rec_box, placement=placement, free_top=free_top, free_bottom=free_bottom
        )
        rec_cy = round(rec_cy, 4)
        layout = (
            {"scale": solved, "center_x_frac": 0.5, "center_y_frac": rec_cy}
            if for_scene == "hero-reveal"
            else {"phone_h_frac": solved, "center_y_frac": rec_cy}
        )
        recommended = {
            "layout": layout,
            "top_px": round(rec_box[1], 1),
            "bottom_px": round(rec_box[3], 1),
            "caption_clearance_px": round(rec_clearance, 1),
            "ok": True,
        }

    return {
        "for_scene": for_scene,
        "ratio": ratio,
        "content_box_src": content_box_src,
        "caption_band": {"top_px": round(band_top, 1), "bottom_px": round(band_bottom, 1)},
        "current": {
            "top_px": round(current_box[1], 1),
            "bottom_px": round(current_box[3], 1),
            "caption_clearance_px": round(current_clearance, 1),
            "ok": current_ok,
        },
        "recommended": recommended,
        "ok": current_ok,
    }


def run_cli(args: argparse.Namespace, kit: dict) -> tuple[dict, int]:
    """``cli.py``'s ``fit-layout`` dispatch, kept here so ``cli.py`` stays a thin wrapper: build
    the report from parsed args and decide the exit code (0 fits, 1 current layout clips or
    overlaps, 2 usage/refusal)."""
    if args.for_scene is None:
        return {"scene": "fit-layout", "error": "--for-scene is required"}, 2
    try:
        doc = report(
            for_scene=args.for_scene,
            kit=kit,
            ratio=args.ratio,
            image=args.image,
            actions=args.actions,
            caption_lines=args.caption_lines,
            margin_px=args.margin_px,
            repo_root=args.repo_root,
        )
    except FitLayoutError as exc:
        return {"scene": "fit-layout", "error": str(exc)}, 2
    return doc, (0 if doc["ok"] else 1)
