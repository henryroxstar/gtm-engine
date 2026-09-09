from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ..video_lint import SAFE_AREAS, SafeArea
from .base import SceneError

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


def _mix_hex(a: str, b: str, t: float) -> str:
    """``a`` blended ``t`` of the way toward ``b``, as a hex string. Used for DERIVED palette
    roles, so a fallback stays inside the tenant's own colours instead of inventing one."""
    ra, ga, ba = _hex_to_rgb(a, field="mix")
    rb, gb, bb = _hex_to_rgb(b, field="mix")
    mixed = (
        round(ra + (rb - ra) * t),
        round(ga + (gb - ga) * t),
        round(ba + (bb - ba) * t),
    )
    return "#{:02X}{:02X}{:02X}".format(*mixed)


def _load_palette(kit: dict) -> _Palette:
    """Resolve the six colors a scene needs from the merged brand kit's ``[palette]``.

    Every field falls back to another palette key before it falls back to a literal, so a kit that
    only defines the basics still renders — a scene should degrade gracefully on a thin kit, the
    same posture ``captions.py`` takes when ``[disclosure]`` is absent, never raise on a MISSING
    nice-to-have.

    THE LITERALS MUST STAY TENANT-NEUTRAL. Until 2026-09-01 they were ``#0B1A2E`` and ``#00C6C6``
    — one specific tenant's deck navy and teal, sitting in engine code. That is a de-brand
    violation, and it was not theoretical: ``profiles/_template`` and a live product kit with a
    warm terracotta-on-cream palette both declare no ``accent``, so a brand-new tenant and that
    product were BOTH rendering their UI chrome in another company's teal. A default that
    quietly impersonates a real brand is worse than one that looks unset, which is why ``accent``
    now degrades to ``primary`` (the kit's own action colour) before reaching any literal, and the
    last-resort literals are achromatic.
    """
    p = kit.get("palette") if isinstance(kit, dict) else None
    p = p if isinstance(p, dict) else {}
    canvas = p.get("canvas") or "#0B0B0F"
    surface = p.get("surface") or canvas
    # accent → primary → neutral grey. Never another tenant's brand colour.
    accent = p.get("accent") or p.get("primary") or "#9AA0A6"
    text = p.get("text") or p.get("ink") or "#FFFFFF"
    return _Palette(
        canvas=canvas,
        surface=surface,
        text=text,
        # DERIVED from the kit, not a literal. Until 2026-09-03 this fell back to `#8FA3B8`, a
        # blue-grey that appears in no brand kit in this repo — so every card in every film drew
        # its secondary text in a colour no tenant had ever declared, and the de-brand fix of
        # 2026-09-01 missed it because a desaturated blue does not look like a brand colour. It is
        # now ink dimmed toward the tenant's own canvas, which is the same hue relationship on
        # every kit and cannot smuggle a hue in.
        text_dim=p.get("text_dim") or p.get("muted") or _mix_hex(text, canvas, 0.38),
        accent=accent,
        # SIGNAL colours, deliberately outside the brand contract and deliberately literal: a row
        # that says "denied" must read as denied in every tenant's palette, so these do not
        # degrade to an accent. Same reasoning, and the same two roles, as the deck theme's
        # `--signal-stop` / `--signal-warn`.
        warn=p.get("warn") or p.get("amber") or "#F2A93B",
        good=p.get("good") or p.get("success") or "#3BD68A",
    )


def _resolve_area(ratio: str) -> SafeArea:
    if ratio not in SAFE_AREAS:
        raise SceneError(f"unknown ratio {ratio!r} — expected one of {sorted(SAFE_AREAS)}")
    return SAFE_AREAS[ratio]


# ── logo compositing — OPT IN, on the film's own brand cards only ──────────────────────────────
#
# NOT wired into every scene, and that is deliberate rather than an oversight to fix later. Per
# this module's own CONTENT BOUNDARY (top of file): `record-*` and `call-ui-*` scenes illustrate a
# FICTIONAL third party's software — a bank's customer record screen, a hotel's booking UI — never
# ours. Compositing our own mark onto a fake bank's screen would claim we built the bank's
# software, which is the opposite of what those scenes exist to argue. Only
# `render_title_card_frames` (title-claim / title-close — the film's own statement, in its own
# voice) accepts `logo=True`. Extend the allowlist deliberately, scene by scene, never globally.


def _logo_background(palette: _Palette) -> str:
    """Classify the frame's ground as "dark" or "light" so the right reversed mark gets picked.

    A coarse sRGB-weighted average, not true relative luminance — brand grounds are chosen to
    read unambiguously as one or the other (no kit sits at a midtone), so the precision a proper
    luminance formula buys is not needed here. Never resolves "blue": no scene in this module
    paints a full-bleed primary-color ground, so `logo_*_blue` in the kit stays for a consumer
    that composites onto a literal blue panel/tile, not this one.
    """
    r, g, b = _hex_to_rgb(palette.canvas, field="canvas")
    return "dark" if (0.299 * r + 0.587 * g + 0.114 * b) < 128 else "light"


def _load_logo_asset(
    kit: dict, *, background: str, variant: str = "horizontal", repo_root: Path | None = None
) -> Path:
    """Resolve the PNG for one ``[assets].logo_<variant>_<background>`` entry.

    The kit stores the SVG path (the vector master, the format every non-Pillow consumer wants);
    this module cannot rasterise SVG, so it resolves the sibling PNG **the BRAND.toml convention
    already promises** — "matching PNGs sit alongside every SVG above (same basename, .png)" —
    rather than requiring a second, PNG-specific kit key that would drift from the SVG one.
    """
    assets = kit.get("assets") if isinstance(kit, dict) else None
    key = f"logo_{variant}_{background}"
    raw = (assets or {}).get(key) if isinstance(assets, dict) else None
    if not raw:
        raise SceneError(
            f"logo requested but the brand kit has no [assets].{key} — vendor the logo pack and "
            f"wire {key} in BRAND.toml, or render this scene with logo=False"
        )
    root = repo_root if repo_root is not None else Path.cwd()
    svg_path = Path(raw)
    if not svg_path.is_absolute():
        svg_path = root / svg_path
    png_path = svg_path.with_suffix(".png")
    if not png_path.is_file():
        raise SceneError(
            f"[assets].{key} names {raw!r}; expected a sibling {png_path.name!r} next to it "
            f"(this module rasterises PNG, never SVG) but {png_path} does not exist"
        )
    return png_path


def _prepare_logo(
    kit: dict,
    *,
    palette: _Palette,
    frame_w: int,
    target_w_frac: float = 0.11,
    variant: str = "horizontal",
    repo_root: Path | None = None,
) -> Image.Image:
    """Load and size the logo ONCE per scene (outside the frame loop), background-matched.

    Returns an RGBA image at the target render width, aspect preserved. Callers fade it in per
    frame by scaling ITS alpha channel (see ``_paste_logo``) rather than re-opening/resizing here
    120 times — the same "prepare once, mutate per frame" shape ``ground``/``face`` already use in
    this module.
    """
    background = _logo_background(palette)
    path = _load_logo_asset(kit, background=background, variant=variant, repo_root=repo_root)
    logo = Image.open(path).convert("RGBA")
    target_w = max(1, round(frame_w * target_w_frac))
    target_h = max(1, round(logo.height * (target_w / logo.width)))
    return logo.resize((target_w, target_h), Image.LANCZOS)


def _paste_logo(frame: Image.Image, logo: Image.Image, *, x: int, y: int, opacity: float) -> None:
    """Alpha-composite ``logo`` onto ``frame`` at ``(x, y)``, scaled by ``opacity`` in [0, 1].

    Scales the ALPHA band rather than blending toward a background color (the way this module
    fades in text, via ``_lerp_color``) because the logo's own pixels already carry per-pixel
    transparency — the crescent cutout in the mark, the letterforms' negative space — and lerping
    toward a flat color would flatten that cutout into a solid tinted shape at partial fade.
    """
    if opacity <= 0:
        return
    if opacity >= 1:
        frame.alpha_composite(logo, dest=(x, y))
        return
    r, g, b, a = logo.split()
    a = a.point(lambda v: round(v * opacity))
    faded = Image.merge("RGBA", (r, g, b, a))
    frame.alpha_composite(faded, dest=(x, y))
