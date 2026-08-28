"""Deterministic, brand-styled UI-mockup frame sequences for the ``screen`` shot role.

THIRD (and last) MODULE IN gtm_core/ PERMITTED TO IMPORT PILLOW. ``captions.py`` carries the
original exception for text rasterization; ``cover_frame.py`` widened it (Phase E) for frame
scoring. This is a further deliberate widening, same shape as both — pixel-level image work stays
confined to named, reviewed modules rather than scattered across the ffmpeg-orchestration layer
(``video_finish.py``) or, worse, into a skill body as prose describing pixels nobody checks.
``tests/media/test_captions_module_boundary.py`` enforces the exact three-module allowlist; adding
a fourth needs the same reasoning here, not just a passing test.

WHY THIS EXISTS AT ALL
-----------------------
``gtm_core.shots_lint._lint_no_in_frame_text`` refuses a generation prompt that asks a video model
to render legible text in frame: diffusion video models paint shapes that *resemble* text rather
than typesetting it, which is where garbled on-screen UI came from before this module existed. But
some shots ARE the payload of on-screen text — a request inspector panel with a blank field
labelled "acting agent" is the argument the shot exists to make, and describing it in prose to a
generation model produces exactly the garbled result the lint bans. The lint's own fix is named in
its message: "let gtm_core.captions burn it in at finish time" — this module is that fix's sibling
for a `screen` shot that is a UI, not a caption. Every pixel is drawn deterministically from code,
so it is checkable before it ships, the same property that makes a caption safe to burn where a
generated video is not.

DELIBERATELY NOT ANIMATED VIA A GENERATION MODEL. A UI mockup with legible text drawn frame-by-
frame from code cannot drift, hallucinate a word, or fail to typeset — cost is zero, and the result
is pixel-identical on a second run given the same inputs. That determinism is the point: this is
the STATIC-LOOKING failure `shots_lint`'s V9 warns about turned into something that genuinely
isn't static — a cursor moves, a value changes state, a field pulses — using the same easing curve
every time rather than a random flourish that would make two renders of the "same" shot differ.

CONTENT BOUNDARY. These mockups are GENERIC illustrations for a narrative beat — a gym booking
app, a request inspector — never a real product's screen. Reusing an actual tenant product
screenshot (profiles/<tenant>/knowledge/brand/product-screenshots/) for a shot illustrating a
generic authorization failure would misrepresent our own product as carrying that flaw; the
INDEX.md there is explicit that those illustrate *our proposed solution*, never a hypothetical
failure case. Brand color/type still comes from the kit so the shot matches the rest of the video.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .captions import FontMissing, load_face
from .video_lint import SAFE_AREAS, SafeArea

__all__ = [
    "SceneError",
    "render_class_booking_frames",
    "render_request_inspector_frames",
]


class SceneError(ValueError):
    """A scene could not be built or written. The message names exactly what is wrong."""


# ── brand resolution ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Palette:
    canvas: str
    surface: str
    text: str
    text_dim: str
    accent: str
    warn: str
    good: str


def _hex_to_rgb(value: str, *, field: str) -> tuple[int, int, int]:
    v = str(value or "").lstrip("#")
    if len(v) != 6:
        raise SceneError(f"palette.{field}={value!r} is not a 6-digit hex color")
    try:
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    except ValueError as exc:
        raise SceneError(f"palette.{field}={value!r} is not a valid hex color") from exc


def _load_palette(kit: dict) -> _Palette:
    """Resolve the six colors a scene needs from the merged brand kit's ``[palette]``.

    Every field has a documented fallback to another palette key rather than a hardcoded literal,
    so a kit that only defines the Mode A basics (canvas/surface/accent, per BRAND.toml's own
    comment) still renders — a scene should degrade gracefully on a thin kit, the same posture
    ``captions.py`` takes when ``[disclosure]`` is absent, never raise on a MISSING nice-to-have.
    """
    p = kit.get("palette") if isinstance(kit, dict) else None
    p = p if isinstance(p, dict) else {}
    canvas = p.get("canvas") or "#0B1A2E"
    surface = p.get("surface") or canvas
    accent = p.get("accent") or "#00C6C6"
    return _Palette(
        canvas=canvas,
        surface=surface,
        text=p.get("text") or p.get("ink") or "#FFFFFF",
        text_dim=p.get("text_dim") or p.get("muted") or "#8FA3B8",
        accent=accent,
        warn=p.get("warn") or p.get("amber") or "#F2A93B",
        good=p.get("good") or p.get("success") or "#3BD68A",
    )


def _resolve_area(ratio: str) -> SafeArea:
    if ratio not in SAFE_AREAS:
        raise SceneError(f"unknown ratio {ratio!r} — expected one of {sorted(SAFE_AREAS)}")
    return SAFE_AREAS[ratio]


# ── easing (deterministic — no randomness, ever: two runs of the same inputs match byte-for-byte
#    in every frame's drawn geometry, which is the whole reason this exists) ───────────────────


def _ease_in_out(t: float) -> float:
    """Cubic ease-in-out. ``t`` in [0, 1] -> eased [0, 1]. A cursor or a color transition that
    starts and ends at rest reads as intentional motion; a linear one reads as a slide-show with
    extra steps, which is the V9 defect this module exists to avoid reproducing by other means."""
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        return 4 * t * t * t
    return 1 - pow(-2 * t + 2, 3) / 2


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_color(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (
        round(_lerp(a[0], b[0], t)),
        round(_lerp(a[1], b[1], t)),
        round(_lerp(a[2], b[2], t)),
    )


def _window(t: float, start: float, end: float) -> float:
    """Map global progress ``t`` to local progress within [start, end], clamped to [0, 1]. The
    building block every phased animation below composes from — a cursor descent, a state flip,
    and a slide-up are each just one call to this with their own window."""
    if end <= start:
        return 1.0 if t >= end else 0.0
    return max(0.0, min(1.0, (t - start) / (end - start)))


# ── drawing primitives ──────────────────────────────────────────────────────────────────────────


def _font(face: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(face), size)


def _text_w(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> float:
    return draw.textlength(text, font=font)


def _write_frames(frames: list[Image.Image], out_dir: Path, *, prefix: str) -> int:
    if not frames:
        raise SceneError("scene produced zero frames")
    out_dir.mkdir(parents=True, exist_ok=True)
    for existing in out_dir.glob(f"{prefix}-*.png"):
        existing.unlink()
    for i, frame in enumerate(frames):
        frame.convert("RGB").save(out_dir / f"{prefix}-{i:04d}.png", "PNG")
    return len(frames)


def _frame_count(fps: int, duration_s: float) -> int:
    if fps <= 0:
        raise SceneError(f"fps must be > 0, got {fps}")
    if duration_s <= 0:
        raise SceneError(f"duration_s must be > 0, got {duration_s}")
    return max(2, round(fps * duration_s))


# ── shot 2 — class-booking app: a full 6pm session with a waitlist beneath it ───────────────────
#
# Animation (matches content/<tenant>/scripts/2026-08-18-gym-incident-ciso-authz.shots.json #2):
# the cursor descends the list, the booked row flips from "Booked · Full" to "Cancelled", and the
# waitlist name beneath it slides up one place to fill the freed slot.


def render_class_booking_frames(
    *,
    kit: dict,
    ratio: str,
    fps: int,
    duration_s: float,
    out_dir: Path,
    font_role: str = "caption",
    repo_root: Path | None = None,
) -> int:
    """Write the numbered PNG sequence for shot 2 into ``out_dir``. Returns the frame count."""
    area = _resolve_area(ratio)
    palette = _load_palette(kit)
    try:
        face = load_face(kit, role=font_role, repo_root=repo_root)
    except FontMissing as exc:
        raise SceneError(str(exc)) from exc

    canvas_rgb = _hex_to_rgb(palette.canvas, field="canvas")
    surface_rgb = _hex_to_rgb(palette.surface, field="surface")
    text_rgb = _hex_to_rgb(palette.text, field="text")
    dim_rgb = _hex_to_rgb(palette.text_dim, field="text_dim")
    accent_rgb = _hex_to_rgb(palette.accent, field="accent")
    good_rgb = _hex_to_rgb(palette.good, field="good")

    w, h = area.width, area.height
    # A phone-app screen inset within the frame, not a full-bleed UI — the shot list calls it "on
    # a phone screen", so the mockup should read as a device screen, not a desktop dashboard.
    margin = round(w * 0.10)
    panel_left, panel_top = margin, round(h * 0.16)
    panel_w, panel_h = w - 2 * margin, round(h * 0.70)

    row_h = round(panel_h * 0.15)
    row_gap = round(row_h * 0.22)
    rows = [
        ("9:00 AM", "Morning Flow", "6 / 12 spots"),
        ("6:00 PM", "Full Body HIIT", "12 / 12 spots"),  # the row that flips
        ("7:30 PM", "Evening Stretch", "3 / 15 spots"),
    ]
    booked_row_index = 1
    waitlist_name = "Waitlist: J. Reyes"
    # Reserved vertical space beneath the booked row for the waitlist line — the shot's own
    # premise ("a full 6pm session with a waitlist beneath it") needs a dedicated slot from
    # frame 0, and this is what keeps that line from colliding with either neighboring card as it
    # moves. Sized to clear a `waitlist_font`-height line with padding on both sides.
    waitlist_strip_h = round(row_h * 0.62)

    # Row tops, precomputed so the strip only widens the gap AFTER the booked row.
    row_tops: list[int] = []
    top = panel_top
    for idx in range(len(rows)):
        row_tops.append(top)
        top += row_h + row_gap
        if idx == booked_row_index:
            top += waitlist_strip_h

    title_font = _font(face, round(h * 0.032))
    row_title_font = _font(face, round(h * 0.024))
    row_sub_font = _font(face, round(h * 0.017))
    badge_font = _font(face, round(h * 0.016))
    waitlist_font = _font(face, round(h * 0.016))

    n_frames = _frame_count(fps, duration_s)
    frames: list[Image.Image] = []

    # Phase windows over the shot's global progress t in [0, 1].
    CURSOR_START, CURSOR_END = 0.05, 0.55  # descend to the 6pm row
    FLIP_START, FLIP_END = 0.55, 0.80  # Booked -> Cancelled, badge color accent -> dim
    SLIDE_START, SLIDE_END = 0.72, 0.98  # waitlist name moves up within its strip, dim -> good

    booked_row_top = row_tops[booked_row_index]
    cursor_y_top = panel_top + round(row_h * 0.5)
    cursor_y_bottom = booked_row_top + round(row_h * 0.5)

    for i in range(n_frames):
        t = i / (n_frames - 1) if n_frames > 1 else 1.0
        img = Image.new("RGB", (w, h), canvas_rgb)
        draw = ImageDraw.Draw(img)

        draw.text((margin, round(h * 0.09)), "Studio Booking", font=title_font, fill=text_rgb)

        flip_t = _ease_in_out(_window(t, FLIP_START, FLIP_END))
        slide_t = _ease_in_out(_window(t, SLIDE_START, SLIDE_END))

        for idx, (time_label, name, spots) in enumerate(rows):
            y = row_tops[idx]
            is_target = idx == booked_row_index
            draw.rounded_rectangle(
                (panel_left, y, panel_left + panel_w, y + row_h),
                radius=round(row_h * 0.18),
                fill=surface_rgb,
            )
            draw.text(
                (panel_left + round(panel_w * 0.04), y + round(row_h * 0.18)),
                time_label,
                font=row_sub_font,
                fill=dim_rgb,
            )
            draw.text(
                (panel_left + round(panel_w * 0.04), y + round(row_h * 0.46)),
                name,
                font=row_title_font,
                fill=text_rgb,
            )

            badge_w, badge_h = round(panel_w * 0.30), round(row_h * 0.34)
            badge_x = panel_left + panel_w - badge_w - round(panel_w * 0.04)
            badge_y = y + round((row_h - badge_h) / 2)
            if is_target:
                badge_color = _lerp_color(accent_rgb, dim_rgb, flip_t)
                badge_text = "Cancelled" if flip_t > 0.5 else "Booked · Full"
            else:
                # A quiet, always-legible outline/text pair — distinct from the target row's
                # accent so the one row that changes state is the one row that reads as "live".
                badge_color = dim_rgb
                badge_text = spots
            draw.rounded_rectangle(
                (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h),
                radius=round(badge_h * 0.5),
                outline=badge_color,
                width=2,
            )
            btw = _text_w(draw, badge_text, badge_font)
            draw.text(
                (badge_x + (badge_w - btw) / 2, badge_y + round(badge_h * 0.18)),
                badge_text,
                font=badge_font,
                fill=badge_color,
            )

            if is_target:
                # Confined entirely to the dedicated strip below this row — never the row itself
                # or the row that follows it, at any point in the animation.
                strip_top = y + row_h
                wl_y_bottom = strip_top + waitlist_strip_h - round(waitlist_strip_h * 0.30)
                wl_y_top = strip_top + round(waitlist_strip_h * 0.22)
                wl_y = round(_lerp(wl_y_bottom, wl_y_top, slide_t))
                wl_color = _lerp_color(dim_rgb, good_rgb, slide_t)
                draw.text(
                    (panel_left + round(panel_w * 0.04), wl_y),
                    waitlist_name,
                    font=waitlist_font,
                    fill=wl_color,
                )

        cursor_t = _ease_in_out(_window(t, CURSOR_START, CURSOR_END))
        cursor_y = round(_lerp(cursor_y_top, cursor_y_bottom, cursor_t))
        cursor_x = panel_left + panel_w - round(panel_w * 0.06)
        _draw_cursor(draw, cursor_x, cursor_y, accent_rgb)

        frames.append(img)

    return _write_frames(frames, out_dir, prefix="shot2-booking")


# ── shot 5 — request inspector: a valid session token, a blank "acting agent" field ─────────────
#
# Animation: the session-token line highlights green (it checked out), the cursor drops to the
# empty "acting agent" field beneath it, and that field pulses amber — the shot's whole argument
# is that the field is blank and nothing stopped the request anyway.


def render_request_inspector_frames(
    *,
    kit: dict,
    ratio: str,
    fps: int,
    duration_s: float,
    out_dir: Path,
    font_role: str = "caption",
    repo_root: Path | None = None,
) -> int:
    """Write the numbered PNG sequence for shot 5 into ``out_dir``. Returns the frame count."""
    area = _resolve_area(ratio)
    palette = _load_palette(kit)
    try:
        face = load_face(kit, role=font_role, repo_root=repo_root)
    except FontMissing as exc:
        raise SceneError(str(exc)) from exc

    canvas_rgb = _hex_to_rgb(palette.canvas, field="canvas")
    surface_rgb = _hex_to_rgb(palette.surface, field="surface")
    text_rgb = _hex_to_rgb(palette.text, field="text")
    dim_rgb = _hex_to_rgb(palette.text_dim, field="text_dim")
    accent_rgb = _hex_to_rgb(palette.accent, field="accent")
    warn_rgb = _hex_to_rgb(palette.warn, field="warn")
    good_rgb = _hex_to_rgb(palette.good, field="good")

    w, h = area.width, area.height
    margin = round(w * 0.10)
    panel_left, panel_top = margin, round(h * 0.18)
    panel_w = w - 2 * margin
    field_h = round(h * 0.11)
    field_gap = round(field_h * 0.30)

    title_font = _font(face, round(h * 0.030))
    label_font = _font(face, round(h * 0.016))
    value_font = _font(face, round(h * 0.021))
    placeholder_font = _font(face, round(h * 0.021))

    fields = [
        ("SESSION TOKEN", "sess_live_8f2c…d91a  ·  valid"),
        ("USER", "member_4471  ·  verified"),
        ("ACTING AGENT", ""),  # the blank field the shot is about
    ]
    acting_index = 2

    n_frames = _frame_count(fps, duration_s)
    frames: list[Image.Image] = []

    HILITE_START, HILITE_END = 0.05, 0.35  # session line highlights green
    DROP_START, DROP_END = 0.30, 0.55  # cursor drops to the acting-agent field
    PULSE_START, PULSE_END = 0.55, 1.0  # blank field pulses amber, repeating

    field_top = {idx: panel_top + idx * (field_h + field_gap) for idx in range(len(fields))}
    cursor_y_top = panel_top + round(field_h * 0.5)
    cursor_y_bottom = field_top[acting_index] + round(field_h * 0.5)

    for i in range(n_frames):
        t = i / (n_frames - 1) if n_frames > 1 else 1.0
        img = Image.new("RGB", (w, h), canvas_rgb)
        draw = ImageDraw.Draw(img)

        draw.text((margin, round(h * 0.09)), "Request Inspector", font=title_font, fill=text_rgb)

        hilite_t = _ease_in_out(_window(t, HILITE_START, HILITE_END))
        pulse_local = _window(t, PULSE_START, PULSE_END)
        # A slow repeating pulse rather than a one-shot fade — the field STAYS unattended for the
        # rest of the shot, so its emphasis should read as ongoing, not as a transition that ends.
        pulse_t = 0.5 + 0.5 * math.sin(pulse_local * 2 * math.pi * 1.5) if pulse_local > 0 else 0.0

        for idx, (label, value) in enumerate(fields):
            y = field_top[idx]
            is_session = idx == 0
            is_acting = idx == acting_index

            border = surface_rgb
            if is_session:
                border = _lerp_color(surface_rgb, good_rgb, hilite_t)
            elif is_acting and pulse_local > 0:
                border = _lerp_color(surface_rgb, warn_rgb, 0.35 + 0.65 * pulse_t)

            draw.rounded_rectangle(
                (panel_left, y, panel_left + panel_w, y + field_h),
                radius=round(field_h * 0.16),
                fill=surface_rgb,
                outline=border,
                width=3,
            )
            draw.text(
                (panel_left + round(panel_w * 0.04), y + round(field_h * 0.16)),
                label,
                font=label_font,
                fill=dim_rgb,
            )
            if value:
                fill = _lerp_color(text_rgb, good_rgb, hilite_t) if is_session else text_rgb
                draw.text(
                    (panel_left + round(panel_w * 0.04), y + round(field_h * 0.46)),
                    value,
                    font=value_font,
                    fill=fill,
                )
            else:
                placeholder_color = (
                    _lerp_color(dim_rgb, warn_rgb, pulse_t) if pulse_local > 0 else dim_rgb
                )
                draw.text(
                    (panel_left + round(panel_w * 0.04), y + round(field_h * 0.46)),
                    "— not set —",
                    font=placeholder_font,
                    fill=placeholder_color,
                )

        cursor_t = _ease_in_out(_window(t, DROP_START, DROP_END))
        cursor_y = round(_lerp(cursor_y_top, cursor_y_bottom, cursor_t))
        cursor_x = panel_left + panel_w - round(panel_w * 0.06)
        _draw_cursor(draw, cursor_x, cursor_y, accent_rgb)

        frames.append(img)

    return _write_frames(frames, out_dir, prefix="shot5-inspector")


def _draw_cursor(draw: ImageDraw.ImageDraw, x: int, y: int, rgb: tuple[int, int, int]) -> None:
    """A simple filled arrow-cursor glyph — legible at UI-mockup scale without a font."""
    size = 16
    points = [
        (x, y),
        (x, y + size),
        (x + size * 0.35, y + size * 0.75),
        (x + size * 0.62, y + size * 1.05),
        (x + size * 0.78, y + size * 0.9),
        (x + size * 0.5, y + size * 0.58),
        (x + size * 0.78, y + size * 0.5),
    ]
    draw.polygon(points, fill=rgb)


# ── CLI ─────────────────────────────────────────────────────────────────────────────────────────

_SCENES = {
    "class-booking": render_class_booking_frames,
    "request-inspector": render_request_inspector_frames,
}


def main(argv: list[str] | None = None) -> int:
    """``python -m gtm_core.screen_ui <scene> --kit-json <path> --ratio 4:5 --fps 24
    --duration-s 3 --out-dir <dir>`` — write the numbered PNG sequence for one scene.

    Skills are markdown executed by the brain; they cannot import Python, so every module here
    needs a CLI to be reachable from a skill body at all (same convention as
    ``render_engines.main``). ``--kit-json`` takes the brand kit as a JSON file rather than a
    ``--profile`` flag: this module has no business resolving profiles itself — the caller reads
    the kit via ``gtm_core.brandkit`` and hands the resolved dict over, the same separation
    ``captions.render`` already keeps from its own callers.
    """
    parser = argparse.ArgumentParser(prog="gtm_core.screen_ui")
    parser.add_argument("scene", choices=sorted(_SCENES))
    parser.add_argument("--kit-json", type=Path, required=True, help="resolved brand kit JSON")
    parser.add_argument("--ratio", required=True, choices=sorted(SAFE_AREAS))
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--duration-s", type=float, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--font-role", default="caption")
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)

    kit = json.loads(args.kit_json.read_text(encoding="utf-8"))
    scene_fn = _SCENES[args.scene]
    try:
        count = scene_fn(
            kit=kit,
            ratio=args.ratio,
            fps=args.fps,
            duration_s=args.duration_s,
            out_dir=args.out_dir,
            font_role=args.font_role,
            repo_root=args.repo_root,
        )
    except SceneError as exc:
        print(json.dumps({"scene": args.scene, "error": str(exc)}))
        return 2

    print(
        json.dumps(
            {
                "scene": args.scene,
                "frames": count,
                "out_dir": str(args.out_dir),
                "fps": args.fps,
                "duration_s": args.duration_s,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
