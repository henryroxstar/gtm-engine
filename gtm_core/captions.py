"""Caption rendering — PIL-to-PNG, never ffmpeg's ``drawtext``/``ass``/``subtitles`` filters.

F1: the operator's local ffmpeg (Homebrew 8.1.1)
is built without libfreetype/libass — ``ffmpeg -filters`` lists none of ``drawtext``, ``ass``,
``subtitles``. Debian's build (the one CI and the Dockerfile use) *does* have them, which means a
drawtext-based design would pass in CI and fail on the one machine every asset is actually made
on. So captions are rendered as transparent PNGs with Pillow, then composited onto the video with
plain ``overlay`` — a filter every ffmpeg build has.

This is the ONLY module in ``gtm_core/`` permitted to import Pillow (enforced by
``tests/media/test_captions_module_boundary.py``); the import is top-level, not lazy, so a
Pillow-less environment fails loudly at import time naming this module, rather than at some
later, harder-to-diagnose call site. :mod:`gtm_core.video_finish` imports this module lazily,
inside its caption stage only, so normalize/cut/grade/encode still work without Pillow installed.

FONT SUPPLY CHAIN (operator decision, this session)
----------------------------------------------------
No font is vendored by this module or bundled with the repo by default. The caption face is
resolved from the tenant's brand kit at ``[typography.font_files].<role>`` (default role
``"caption"``) via :func:`load_face`, which raises :class:`FontMissing` naming the configured
path when the key is absent or the file does not exist — never a bitmap-font fallback, because a
silent substitute would ship on-brand-looking captions that are not on-brand. The recommended
caption face is Inter (already in every profile's ``[typography].fallback_stack``); the operator
supplies the ``.ttf``.

RAQM / COMPLEX SCRIPT LIMITATION
---------------------------------
``PIL.features.check("raqm")`` is False in this environment — no complex text shaping. Latin,
CJK, and emoji measure correctly via real glyph advance widths. RTL scripts (Hebrew, Arabic) and
complex Indic scripts (Devanagari, Bengali, Tamil, …) would render with wrong ordering/shaping, so
:func:`render` scans for characters in those Unicode blocks and raises
:class:`UnshapeableScript` naming the offending text rather than shipping visibly broken captions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .video_lint import SAFE_AREAS, SafeArea, face_band

# Re-exported, not redefined: this module is still WordTiming's public home (every existing caller
# says `captions.WordTiming`), but the definition lives in a Pillow-free module so
# `gtm_core.vo_timings` can consume it without dragging Pillow onto the vendor-ingest path.
from .word_timing import WordTiming

#: Where the caption block sits INSIDE the safe box. ``upper``/``lower`` hug the reserved margin
#: they are named for; ``center`` is the historical behaviour and is retained only because a
#: face-free asset (a pure screen capture) can legitimately want it.
#:
#: ``center`` is the documented defect (2026-08-18): the safe area reserves EDGES, so centring in
#: it is simultaneously the furthest point from every margin and squarely on the speaker's mouth.
#: For 4:5 that computed to y=595 in a 1350px frame. ``gtm_core.video_lint``'s V3
#: ``caption_over_face`` rule catches the combination of ``center`` and a rendered identity.
PLACEMENTS: tuple[str, ...] = ("upper", "center", "lower")

#: Default placement. ``lower`` is the lower-third convention every talking-head short uses: it
#: keeps text near the face without covering it, and sits above the reserved bottom margin where
#: platform UI chrome lives.
DEFAULT_PLACEMENT = "lower"

#: Reap system caption presets whose own catalogue entry describes UPPER placement. Read live from
#: Reap's ``get_caption_styles`` on 2026-08-19 — not inferred from the id, which says nothing.
#:
#: This exists so a tenant that already declares a preset does not have to declare placement
#: twice. One live tenant kit's ``[captions].preset = "system_indigo"`` was ALREADY an
#: upper-placement choice while the local Pillow path centred the text over the speaker's face —
#: the brand kit had the right answer and the renderer never read it.
_UPPER_PLACEMENT_PRESETS = frozenset(
    {
        "system_crimson",
        "system_ember",
        "system_ember_duo",
        "system_halo",
        "system_indigo",
        "system_lumina",
        "system_prism",
        "system_ticker",
        "system_trophy",
    }
)


def resolve_placement(
    kit: dict, *, default: str = DEFAULT_PLACEMENT, ratio: str | None = None
) -> str:
    """Caption placement for a merged brand kit.

    Precedence: an explicit ``[captions].placement`` wins; otherwise a declared
    ``[captions].preset`` known to be upper-placement implies ``"upper"``; otherwise ``default``.
    An unrecognised explicit value falls back to ``default`` rather than raising — placement is a
    layout preference, and a typo should not block a finish run (unlike the caption FONT, which
    fails loudly because a silent substitute would ship off-brand type).

    ``ratio``, when given, applies the one geometric constraint that outranks preference: on 9:16
    and 16:9 the face band starts above the safe box, so ``upper`` has no room clear of a
    presenter's head and resolves to ``lower`` instead. Without this a kit whose preset implies
    ``upper`` — ``system_indigo`` does — silently produced captions across the forehead on every
    vertical presenter asset, which is the 2026-08-28 three-questions-p1 defect.
    """
    section = kit.get("captions") if isinstance(kit, dict) else None
    if not isinstance(section, dict):
        return default
    explicit = str(section.get("placement") or "").strip().lower()
    if explicit in PLACEMENTS:
        resolved = explicit
    elif str(section.get("preset") or "").strip() in _UPPER_PLACEMENT_PRESETS:
        resolved = "upper"
    else:
        resolved = default
    if resolved == "upper" and ratio and not upper_placement_fits(ratio):
        return "lower"
    return resolved


def upper_placement_fits(ratio: str) -> bool:
    """Whether ``upper`` has any vertical room above the face band at this ratio."""
    area = SAFE_AREAS.get(ratio)
    if area is None:
        return True
    return (_placement_height_budget(area, "upper") or 0.0) >= ERROR_FLOOR_PX


#: Below this, caption text is not legibly readable on a phone screen (~3.1% of a 1080px frame).
ERROR_FLOOR_PX = 34
#: Starting point for the fit-shrink search — the caption max size at 1080 width.
#:
#: Lowered 72 -> 62 on 2026-08-20 (PRD W5) to match Reap's own published caption spec: ~48-62px at
#: 1080 width, 28-36 characters per line, at most two lines. The oversized default is what forced
#: two-line wrapping into the face band, which then forced the placement clamp — the geometry
#: defect and the type-size defect were the same defect, approached from opposite ends. We were
#: hand-rolling captions against a vendor spec we already had and had not read.
BRAND_MAX_PX = 62
#: Reap's published legibility band, used by the fit search and reported by the linter. Not a hard
#: refusal: a short hook line at 62px can legitimately sit under 28 characters.
CHARS_PER_LINE_MIN = 28
CHARS_PER_LINE_MAX = 36
#: A third line means the viewer is reading, not watching. Reap's spec and our own V7 reading
#: budget agree on this one.
MAX_CAPTION_LINES = 2
#: Line spacing multiplier applied to the font's own line height.
LINE_SPACING = 1.2
#: Default hold, in seconds, for a disclosure line burned onto an asset's own final frames
#: (see with_disclosure). Long enough to read once, short enough to stay inside a real caption
#: beat rather than reading as a dedicated "outro" segment.
DEFAULT_DISCLOSURE_HOLD_S = 2.5

#: Unicode blocks that need complex shaping (bidi reordering / glyph joining) that raqm=False
#: cannot provide. Conservative and named, not exhaustive — extend deliberately, not by guessing.
_UNSHAPEABLE_RANGES: tuple[tuple[int, int, str], ...] = (
    (0x0590, 0x05FF, "Hebrew"),
    (0x0600, 0x06FF, "Arabic"),
    (0x0750, 0x077F, "Arabic Supplement"),
    (0x0900, 0x097F, "Devanagari"),
    (0x0980, 0x09FF, "Bengali"),
    (0x0A00, 0x0A7F, "Gurmukhi"),
    (0x0B80, 0x0BFF, "Tamil"),
    (0x0C00, 0x0C7F, "Telugu"),
    (0x0E00, 0x0E7F, "Thai"),
)


class FontMissing(RuntimeError):
    """The configured caption font path is absent, unreadable, or not configured at all."""


class DoesNotFit(RuntimeError):
    """No font size down to ERROR_FLOOR_PX lets this text fit the safe area."""


class UnshapeableScript(RuntimeError):
    """Text contains a script raqm=False cannot correctly shape."""


@dataclass(frozen=True)
class Screen:
    text: str
    start_s: float
    end_s: float
    estimated: bool = False


@dataclass(frozen=True)
class RenderedScreen:
    screen: Screen
    png_path: Path
    font_px: int
    lines: tuple[str, ...]
    box: dict  # {"x": int, "y": int, "w": int, "h": int} — pixel bounds within the frame
    font_path: Path


@dataclass(frozen=True)
class FaceSet:
    """One font file per semantic weight role, all from ``[typography.font_files]``.

    ``caption`` is the only role a kit must vendor; every other field falls back to it. So a
    tenant with a single face renders byte-identically to before this existed, and a tenant that
    vendors a ladder gets weight contrast instead of size-and-opacity contrast alone — the
    constraint ``screen_ui``'s ``_LABEL_TRACKING`` was written to work around.
    """

    caption: Path
    body: Path
    label: Path
    heading: Path


def load_faces(kit: dict, *, repo_root: Path | None = None) -> FaceSet:
    """Resolve the weight ladder, falling back to ``caption`` for any role the kit omits.

    An OMITTED role falls back silently — that is the single-face tenant, and it is fine. A role
    that IS declared but names an unreadable file raises, exactly as ``load_face`` would: a typo'd
    path is a config error, and silently substituting another weight would hide it in output
    nobody re-reads.
    """
    base = load_face(kit, role="caption", repo_root=repo_root)
    declared = (
        ((kit.get("typography") or {}).get("font_files") or {}) if isinstance(kit, dict) else {}
    )

    def _role(name: str) -> Path:
        if not declared.get(name):
            return base
        return load_face(kit, role=name, repo_root=repo_root)

    return FaceSet(
        caption=base,
        body=_role("body"),
        label=_role("label"),
        heading=_role("heading"),
    )


def load_face(kit: dict, *, role: str = "caption", repo_root: Path | None = None) -> Path:
    """Resolve the caption font file from a merged brand kit. Raises FontMissing naming the
    configured (or absent) path — never falls back to a bitmap font."""
    typography = kit.get("typography") if isinstance(kit, dict) else None
    raw = (typography or {}).get("font_files", {}).get(role) if typography else None
    if not raw:
        raise FontMissing(
            f"BRAND.toml has no [typography.font_files].{role} entry — captions.py requires an "
            "explicit font-file path and never falls back to a bitmap font. Add the key, e.g. "
            f'[typography.font_files]\\n{role} = "profiles/<tenant>/knowledge/brand/fonts/Inter-Bold.ttf"'
        )
    root = repo_root if repo_root is not None else Path.cwd()
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    if not candidate.is_file():
        raise FontMissing(
            f"[typography.font_files].{role} names {raw!r}, resolved to {candidate}, but no "
            "readable file exists there"
        )
    return candidate


def _unshapeable_char(text: str) -> tuple[str, str] | None:
    for ch in text:
        cp = ord(ch)
        for lo, hi, name in _UNSHAPEABLE_RANGES:
            if lo <= cp <= hi:
                return ch, name
    return None


def _safe_box_px(area: SafeArea) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the reserved caption box within the frame, in pixels."""
    x = round(area.width * area.left)
    y = round(area.height * area.top)
    w = round(area.width * (1 - area.left - area.right))
    h = round(area.height * (1 - area.top - area.bottom))
    return x, y, w, h


def _block_top(box_y: int, box_h: int, block_height: float, placement: str) -> float:
    """Vertical origin of the caption block inside the safe box, per placement.

    ``upper``/``lower`` sit flush against the safe box's own top/bottom edge, so the block stays
    inside the reserved area (V3's existing containment check still holds) while staying clear of
    the face band. ``center`` reproduces the historical centring.
    """
    if placement == "upper":
        return float(box_y)
    if placement == "lower":
        return float(box_y + box_h - block_height)
    return box_y + (box_h - block_height) / 2


def overlaps_face_band(top: float, height: float, frame_height: int, ratio: str) -> bool:
    """Whether a caption block at ``top`` of ``height`` px intrudes on the presenter's face band.

    Shared with :mod:`gtm_core.video_lint` (same table) so the renderer can warn at render time
    and the linter can fail the finished asset on the identical arithmetic. ``ratio`` is required
    because the band is per-ratio: a 9:16 full-bleed presenter's head sits far higher in frame
    than a 4:5 medium-close-up's, and the single global band that predated this missed it.
    """
    band_top, band_bottom = face_band(ratio)
    face_y0 = frame_height * band_top
    face_y1 = frame_height * band_bottom
    return top < face_y1 and (top + height) > face_y0


#: Pixels at or above this 0-255 luma inside a caption crop are assumed to BE the glyphs, and are
#: excluded when measuring what sits behind them. Captions render white or brand-accent on
#: transparency; without this the text would measure its own contrast against itself.
_GLYPH_LUMA_FLOOR = 200
#: Manhattan RGB distance within which a pixel counts as glyph rather than backdrop.
_GLYPH_COLOR_TOLERANCE = 150


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    """WCAG 2.x relative luminance from 0-255 sRGB. Linearised — a raw ``mean/255`` is NOT a
    relative luminance and understates contrast badly in the mid-tones (luma 85 reads as 2.7:1
    unlinearised and 7.5:1 correctly), which is exactly the error that produced a false
    "every caption fails AA" report on 2026-08-28."""

    def _lin(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (_lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_against_backdrop(
    png_bytes: bytes, *, glyph_rgb: tuple[int, int, int] = (255, 255, 255)
) -> dict | None:
    """WCAG contrast ratio of ``glyph_rgb`` against what sits BEHIND it in a caption-box crop.

    Pure pixel work on bytes — the caller does the seeking and cropping. Glyph-bright pixels are
    dropped before averaging: an average over the whole crop would include the glyphs themselves,
    which are the brightest thing in it, and would report the caption as higher-contrast than it
    is. Failing conservatively in the wrong direction is worse than not measuring.

    Lives here rather than in :mod:`gtm_core.video_lint` because it is pixel work, and pixel work
    lives in the three allowlisted modules — the same reason :func:`overlaps_face_band` is here.
    Returns ``None`` if the bytes are not a decodable image."""
    import io

    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    except Exception:  # noqa: BLE001 — a corrupt sample must not sink the caller's whole pass
        return None

    gr, gg, gb = glyph_rgb
    raw = img.tobytes()  # not getdata(): deprecated in Pillow 11, removed in 14
    sums = [0, 0, 0]
    kept = 0
    for off in range(0, len(raw) - 2, 3):
        r, g, b = raw[off], raw[off + 1], raw[off + 2]
        # Is this pixel a GLYPH? Judged by nearness to the caller's own glyph colour, not by
        # brightness. A fixed luma floor assumed glyphs are always the bright thing, which was
        # true only while this module hardcoded white type; once captions flip to DARK glyphs on
        # a light backdrop (see _resolve_glyph_rgba) a brightness test discards the BACKDROP and
        # averages the type, inverting the very measurement V11 exists to make.
        if abs(r - gr) + abs(g - gg) + abs(b - gb) > _GLYPH_COLOR_TOLERANCE:
            sums[0] += r
            sums[1] += g
            sums[2] += b
            kept += 1
    if kept:
        mean = (sums[0] / kept, sums[1] / kept, sums[2] / kept)
    else:
        # Every pixel matched the glyph colour: the backdrop is indistinguishable from the
        # type, which is its own failure.
        n = max(1, len(raw) // 3)
        mean = (sum(raw[0::3]) / n, sum(raw[1::3]) / n, sum(raw[2::3]) / n)

    lo, hi = sorted((_relative_luminance(mean), _relative_luminance(glyph_rgb)))
    return {"ratio": round((hi + 0.05) / (lo + 0.05), 2), "bg_luma": round(sum(mean) / 3, 1)}


def backdrop_luma(png_bytes: bytes) -> float | None:
    """Mean WCAG relative luminance of a caption-box crop. Pure pixel work on bytes — the caller
    does the seeking and cropping, the same split :func:`contrast_against_backdrop` uses and the
    reason both live here rather than in the ffmpeg modules (see
    tests/media/test_captions_module_boundary.py).

    Feeds :func:`render`'s ``backdrop_luma``, which picks glyph colour from it. Returns ``None``
    on undecodable bytes so the caller degrades to the default rather than failing a burn."""
    import io

    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB").resize((32, 32))
    except Exception:  # noqa: BLE001 — advisory measurement, never sinks the caller
        return None
    px = list(img.getdata())
    n = max(1, len(px))
    return _relative_luminance(tuple(sum(p[i] for p in px) / n for i in range(3)))


def caption_box_crop(ratio: str, placement: str) -> tuple[int, int, int, int] | None:
    """(w, h, x, y) of the region a caption will actually cover, for an ffmpeg ``crop=``.

    Half the safe box, not the whole frame: a film that is dark overall but light exactly where
    the type lands is the case a frame-wide average gets wrong."""
    if ratio not in SAFE_AREAS:
        return None
    area = SAFE_AREAS[ratio]
    x = round(area.width * area.left)
    w = round(area.width * (1 - area.left - area.right))
    y = round(area.height * area.top)
    h = round(area.height * (1 - area.top - area.bottom))
    if placement == "lower":
        y, h = y + h // 2, h // 2
    elif placement == "upper":
        h = h // 2
    return w, h, x, y


def _placement_height_budget(area: SafeArea, placement: str) -> float | None:
    """Vertical room a block actually has at this placement, or ``None`` for the full safe box.

    Only ``upper`` is constrained: it hugs the top margin, so everything below the face band's
    top edge is unavailable to it. ``lower`` hugs the bottom margin and the face band ends well
    above that, so the safe box is already the binding constraint. ``center`` is unconstrained
    because it is *defined* as sitting on the face — it exists only for face-free assets.

    Returns ``0.0`` when ``upper`` has no room at all, which is the honest answer for 9:16 and
    16:9: their face bands start ABOVE the safe box, so no upper block clears a presenter's head.
    The caller decides what to do about it — clamping to 1px and rendering an illegible sliver,
    as this did before 2026-08-28, is the one thing it must not do silently.
    """
    if placement != "upper":
        return None
    _, box_y, _, _ = _safe_box_px(area)
    band_top, _ = face_band(area.ratio)
    return max(0.0, area.height * band_top - box_y)


def _wrap_greedy(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width_px: float,
    draw: ImageDraw.ImageDraw,
    *,
    max_chars: int | None = CHARS_PER_LINE_MAX,
) -> list[str]:
    """Greedy wrap under BOTH a pixel width and a character count.

    The character ceiling is Reap's published spec (28-36 chars/line) and it is not redundant
    with the pixel width: a narrow typeface clears the safe box at 40+ characters per line and is
    still a wall of text on a phone. Width protects the frame; character count protects the
    reader.
    """
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        too_wide = draw.textlength(candidate, font=font) > max_width_px
        too_long = max_chars is not None and len(candidate) > max_chars
        if (not too_wide and not too_long) or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def _wrap_balanced(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width_px: float,
    draw: ImageDraw.ImageDraw,
    *,
    max_chars: int | None = CHARS_PER_LINE_MAX,
) -> list[str]:
    """The same break points as :func:`_wrap_greedy`, chosen for RAG instead of for greed.

    Greedy wrapping fills each line to the limit and gives the remainder to the last one, which on
    a two-line caption reliably produces a long line over an orphan: "I build all week. Nobody
    hears" / "about it." Both lines fit; the shape is wrong, and on a burned caption the shape is
    most of what the viewer registers before they read a word.

    This keeps the LINE COUNT greedy chose — the ceiling is a legibility rule and is not traded
    away — and then picks, among every split into that many lines, the one with the least
    raggedness. Breaking after a sentence end is rewarded, because a break that coincides with a
    full stop reads as intended rather than as running out of room: the same caption becomes
    "I build all week." / "Nobody hears about it."
    """
    words = text.split()
    if not words:
        return []
    target = len(_wrap_greedy(text, font, max_width_px, draw, max_chars=max_chars))
    #: The search below enumerates splits, which is exponential in the worst case. A burned caption
    #: is short by construction (`split_screens_segmented` chunks it first), so this is a guard
    #: against a caller passing a paragraph, not a real limit on any caption this renders.
    if target <= 1 or len(words) > 24:
        return _wrap_greedy(text, font, max_width_px, draw, max_chars=max_chars)

    def fits(chunk: list[str]) -> bool:
        line = " ".join(chunk)
        if max_chars is not None and len(line) > max_chars:
            return False
        return draw.textlength(line, font=font) <= max_width_px

    best: tuple[float, list[str]] | None = None

    def walk(start: int, lines: list[list[str]]) -> None:
        nonlocal best
        if len(lines) == target:
            if start != len(words):
                return
            widths = [draw.textlength(" ".join(c), font=font) for c in lines]
            slack = sum((max_width_px - w) ** 2 for w in widths[:-1])
            bonus = sum(
                max_width_px**2 * 0.35
                for c in lines[:-1]
                if c[-1].endswith((".", "?", "!", ":", ";"))
            )
            score = slack - bonus
            if best is None or score < best[0]:
                best = (score, [" ".join(c) for c in lines])
            return
        if start >= len(words):
            return
        for end in range(start + 1, len(words) + 1):
            chunk = words[start:end]
            if not fits(chunk):
                break
            walk(end, [*lines, chunk])

    walk(0, [])
    return best[1] if best else _wrap_greedy(text, font, max_width_px, draw, max_chars=max_chars)


def fit(
    text: str,
    *,
    area: SafeArea,
    face: Path,
    max_px: int = BRAND_MAX_PX,
    min_px: int = ERROR_FLOOR_PX,
    max_height_px: float | None = None,
    max_lines: int = MAX_CAPTION_LINES,
) -> tuple[list[str], int]:
    """Greedy-wrap ``text`` at the largest font size (stepping down by 2px) that fits inside the
    safe area's reserved caption box. Raises DoesNotFit naming the offending token when nothing
    down to ``min_px`` fits.

    ``max_height_px`` tightens the vertical budget below the safe box's own height. Upper
    placement needs this: the safe box starts at 8% of frame height but the face band starts at
    22%, so an upper-placed block has only 14% of the frame to live in — 189px at 4:5, which fits
    one line of brand-size type and not two. Without the tighter budget ``fit`` happily returns a
    two-line block that clears the *safe box* and lands on the presenter's forehead, which is
    exactly what ``video_lint``'s V3 then failed the finished file for."""
    bad = _unshapeable_char(text)
    if bad is not None:
        ch, name = bad
        raise UnshapeableScript(
            f"text contains {name} character {ch!r} — raqm is unavailable in this environment, "
            "so complex-script shaping is not supported"
        )

    _, _, safe_w, safe_h = _safe_box_px(area)
    if max_height_px is not None:
        safe_h = min(safe_h, max_height_px)
    scratch = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

    font_px = max_px
    while font_px >= min_px:
        font = ImageFont.truetype(str(face), font_px)
        lines = _wrap_balanced(text, font, safe_w, scratch)
        ascent, descent = font.getmetrics()
        line_height = (ascent + descent) * LINE_SPACING
        block_height = len(lines) * line_height
        widest = max((scratch.textlength(line, font=font) for line in lines), default=0)
        if block_height <= safe_h and widest <= safe_w and len(lines) <= max_lines:
            return lines, font_px
        font_px -= 2

    # Nothing fit even at the floor — name the widest offending token.
    font = ImageFont.truetype(str(face), min_px)
    lines = _wrap_balanced(text, font, safe_w, scratch)
    widest_line = max(lines, key=lambda line: scratch.textlength(line, font=font))
    width = scratch.textlength(widest_line, font=font)
    if len(lines) > max_lines:
        raise DoesNotFit(
            f"caption needs {len(lines)} lines at the {min_px}px floor but the ceiling is "
            f"{max_lines} ({len(text.split())} words: {text!r}). A third line means the viewer is "
            "reading rather than watching — split the text into more screens instead of shrinking "
            "type nobody can read."
        )
    raise DoesNotFit(
        f"{widest_line!r} measures {width:.0f}px at the {min_px}px floor — safe width is "
        f"{safe_w}px. No smaller size is legible; split the text into more screens."
    )


def split_screens(
    text: str,
    *,
    max_words: int = 4,
    total_s: float | None = None,
) -> list[Screen]:
    """Split raw caption text into ~max_words-word screens. When total_s is given, screen
    durations are apportioned evenly across it (estimated=True) — real per-word timing (Reap
    transcribe, Phase E) supersedes this and is not estimated."""
    words = text.split()
    if not words:
        return []
    chunks = [words[i : i + max_words] for i in range(0, len(words), max_words)]
    if total_s is None:
        return [Screen(text=" ".join(c), start_s=0.0, end_s=0.0, estimated=True) for c in chunks]

    per_screen = total_s / len(chunks)
    screens = []
    for i, c in enumerate(chunks):
        start = round(i * per_screen, 3)
        end = round((i + 1) * per_screen, 3) if i < len(chunks) - 1 else round(total_s, 3)
        screens.append(Screen(text=" ".join(c), start_s=start, end_s=end, estimated=True))
    return screens


def with_disclosure(
    screens: list[Screen],
    line: str,
    *,
    total_s: float,
    hold_s: float = DEFAULT_DISCLOSURE_HOLD_S,
) -> list[Screen]:
    """Append ONE more Screen carrying a required disclosure line (EU AI Act Article 50), held
    over the LAST ``hold_s`` seconds of the asset's OWN existing runtime — an overlay on real
    frames that are already there, never a separately appended segment.

    This is the direct fix for the "black disclosure card" defect: the earlier ad-hoc pipeline
    appended a 2s black card after the finished video, which cost runtime with no content in it
    (12% of a 15s asset) and read as dead air killing the loop. Since a caption is just an
    overlay on
    frames that already exist, disclosure needs no extra runtime at all — it rides on the video's
    own closing seconds.

    ``total_s`` is the asset's total duration and is REQUIRED (not inferred) — this function has
    no way to know the real duration on its own, and a wrong guess would either clip the
    disclosure off-screen or place it mid-video. Raises ``ValueError`` for a non-positive
    ``total_s`` rather than silently placing a disclosure screen with a negative or zero-length
    window.  A ``hold_s`` longer than ``total_s`` clamps to the whole asset (``start_s=0``) rather
    than raising — a very short asset still gets full disclosure coverage, it just isn't a clean
    2.5s window.

    Returns ``screens`` unchanged (a new list, not mutated in place) when ``line`` is falsy — no
    disclosure required, nothing appended.
    """
    if not line:
        return list(screens)
    if total_s <= 0:
        raise ValueError(f"total_s must be positive to place a disclosure screen, got {total_s!r}")
    start = max(0.0, total_s - hold_s)
    # Clear the window first. Appending without truncating draws the disclosure ON TOP of whatever
    # caption still runs there — verified 2026-08-19, where the closing caption and "Made with AI.
    # Reviewed and posted by a human." rendered over each other into an unreadable stack. That is
    # not a cosmetic bug: Article 50 disclosure is the one line that must stay legible, and
    # `validate_disclosure` checks the TEXT is present, never that it can be read.
    kept: list[Screen] = []
    for sc in screens:
        if sc.start_s >= start:
            continue  # entirely inside the disclosure window
        if sc.end_s > start:
            sc = replace(sc, end_s=round(start, 3))
        kept.append(sc)
    return [
        *kept,
        Screen(text=line, start_s=round(start, 3), end_s=round(total_s, 3), estimated=False),
    ]


def split_screens_segmented(segments: list[dict], *, max_words: int = 4) -> list[Screen]:
    """Split per-shot caption lines, each inside its OWN time window.

    :func:`split_screens` chunks one concatenated blob and apportions it evenly across the asset.
    That is right for a single-clip script and wrong for a shot list: the chunk boundaries fall
    wherever every fourth word lands, so a screen straddles two shots' captions and reads as a
    fragment of each. Verified 2026-08-19 on a six-shot asset, where "IT CANCELLED SOMEONE ELSE'S
    SPOT" and "NOBODY HAD LOCKED THAT DOOR" rendered as "...ELSE'S SPOT NOBODY HAD", over a shot
    whose voice-over was saying neither.

    Each segment is ``{"text": str, "start_s": float, "end_s": float}`` — the shot's own window,
    so caption and voice-over stay in step by construction rather than by even division.
    """
    screens: list[Screen] = []
    for seg in segments:
        text = str(seg.get("text", "") or "").strip()
        start = float(seg["start_s"])
        end = float(seg["end_s"])
        if not text or end <= start:
            continue
        words = text.split()
        # Balance the chunks instead of taking max_words until the tail runs out. Fixed-size
        # chunking leaves an orphan: a 5-word line splits 4+1 and the last screen is one word held
        # for half the shot ("DOOR", "SPOT", "ACTING" — observed 2026-08-19). Same screen count,
        # evenly filled.
        n_chunks = max(1, -(-len(words) // max_words))
        base, extra = divmod(len(words), n_chunks)
        chunks, cut = [], 0
        for i in range(n_chunks):
            size = base + (1 if i < extra else 0)
            chunks.append(words[cut : cut + size])
            cut += size
        per = (end - start) / len(chunks)
        for i, c in enumerate(chunks):
            a = start + i * per
            b = end if i == len(chunks) - 1 else start + (i + 1) * per
            screens.append(
                Screen(text=" ".join(c), start_s=round(a, 3), end_s=round(b, 3), estimated=True)
            )
    return screens


def split_screens_from_word_timings(
    word_timings: list[WordTiming], *, max_words: int = 4
) -> list[Screen]:
    """Group real per-word timings (e.g. Reap ``transcribe`` output) into ~max_words-word
    screens. Unlike :func:`split_screens`'s ``total_s`` path, each screen's ``start_s``/``end_s``
    come from its own first/last word's REAL timing, not an even apportionment across the whole
    duration — ``estimated`` is therefore ``False``. Empty input returns no screens."""
    if not word_timings:
        return []
    chunks = [word_timings[i : i + max_words] for i in range(0, len(word_timings), max_words)]
    screens = []
    for chunk in chunks:
        text = " ".join(w.word for w in chunk)
        screens.append(
            Screen(text=text, start_s=chunk[0].start_s, end_s=chunk[-1].end_s, estimated=False)
        )
    return screens


def _hex_to_rgba(value: str) -> tuple[int, int, int, int] | None:
    """Parse a '#RRGGBB' (or '#RRGGBBAA') brand color. Returns None for anything else — a
    malformed palette value degrades to no emphasis coloring, never a crash mid-render."""
    if not isinstance(value, str):
        return None
    v = value.strip().lstrip("#")
    if len(v) not in (6, 8):
        return None
    try:
        r, g, b = int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
        a = int(v[6:8], 16) if len(v) == 8 else 255
    except ValueError:
        return None
    return (r, g, b, a)


def _resolve_accent_rgba(kit: dict) -> tuple[int, int, int, int] | None:
    """Best-effort brand accent color for keyword emphasis, from ``[palette].accent``. Absent or
    unparseable is not an error here — :func:`render`'s ``emphasis`` degrades to plain white text
    rather than refusing to render, since emphasis is a nice-to-have, not a disclosure-grade fact
    (unlike :func:`load_face`, which never falls back)."""
    palette = kit.get("palette") if isinstance(kit, dict) else None
    accent = (palette or {}).get("accent") if isinstance(palette, dict) else None
    return _hex_to_rgba(accent) if accent else None


#: Backdrop relative luminance at or above which the caption flips to DARK glyphs. 0.40 sits well
#: clear of both clusters actually measured on real assets — a lamplit library interior reads
#: ~0.05-0.13, the app's own near-white UI reads ~0.88 — so the decision is never marginal.
_DARK_GLYPH_LUMA_THRESHOLD = 0.40
#: Scrim opacity. Enough to carry a mid-tone backdrop past WCAG AA, light enough
#: that the picture still reads through it.
SCRIM_ALPHA = 150
_FALLBACK_LIGHT_GLYPH = (255, 255, 255, 255)
_FALLBACK_DARK_GLYPH = (30, 28, 22, 255)  # neutral near-black; overridden by [palette].ink


def _resolve_glyph_rgba(
    kit: dict, backdrop_luma: float | None
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    """Pick (glyph, stroke) from the tenant's palette and the MEASURED backdrop.

    Until 2026-09-03 this module drew every caption in hardcoded pure white and never consulted
    the kit at all — while the kit advertised a preset named ``bao-lower-cream``. On a dark film
    that is invisible as a defect; on a light one it is invisible as a caption. Measured on a real
    asset: white-on-app-UI came out at **1.10:1** (WCAG AA wants 4.5:1), i.e. burned type that is
    physically present and cannot be read, plus two more captions at 3.97:1.

    The stroke is the belt-and-braces half and is not decoration: it is the OPPOSITE tone, so even
    if ``backdrop_luma`` is unrepresentative of the pixels actually behind the glyphs — a busy or
    mixed backdrop, a shot that changes brightness under the caption — the type still separates
    from the picture. Contrast then degrades gracefully instead of vanishing."""
    palette = kit.get("palette") if isinstance(kit, dict) else None
    palette = palette if isinstance(palette, dict) else {}
    light = _hex_to_rgba(palette.get("canvas") or "") or _FALLBACK_LIGHT_GLYPH
    dark = _hex_to_rgba(palette.get("ink") or "") or _FALLBACK_DARK_GLYPH
    if backdrop_luma is None:
        # Unmeasured: prior behaviour (light glyphs), but still stroked.
        return _FALLBACK_LIGHT_GLYPH, dark
    if backdrop_luma >= _DARK_GLYPH_LUMA_THRESHOLD:
        return dark, light
    return light, dark


def _draw_line_with_emphasis(
    draw: ImageDraw.ImageDraw,
    line: str,
    x: float,
    y: float,
    *,
    font: ImageFont.FreeTypeFont,
    emphasis_lower: set[str],
    accent_rgba: tuple[int, int, int, int],
    glyph_rgba: tuple[int, int, int, int],
    stroke_rgba: tuple[int, int, int, int],
    stroke_w: int,
) -> None:
    """Draw one wrapped line word-by-word, coloring an emphasized word in the brand accent.
    Space width is measured once and reused (a single space's width is stable within one font/
    size) rather than re-measuring per gap."""
    space_w = draw.textlength(" ", font=font)
    cursor = x
    for word in line.split(" "):
        bare = word.strip(".,!?;:\"'").lower()
        fill = accent_rgba if bare in emphasis_lower else glyph_rgba
        draw.text(
            (cursor, y),
            word,
            font=font,
            fill=fill,
            stroke_width=stroke_w,
            stroke_fill=stroke_rgba,
        )
        cursor += draw.textlength(word, font=font) + space_w


def render(
    screens: list[Screen],
    *,
    ratio: str,
    kit: dict,
    out_dir: Path,
    repo_root: Path | None = None,
    emphasis: frozenset[str] = frozenset(),
    placement: str = DEFAULT_PLACEMENT,
    backdrop_luma: float | None = None,
) -> list[RenderedScreen]:
    """Render each screen to a transparent PNG sized to the frame, positioned inside the safe
    caption box. Raises FontMissing / DoesNotFit / UnshapeableScript per-screen.

    ``emphasis`` is a set of words (case-insensitive, punctuation-stripped match) to draw in the
    brand's ``[palette].accent`` color instead of white — a keyword-emphasis pass over the same
    geometry ``fit()`` already computed, never a second wrap/fit. Absent an accent color, matched
    words still render, just not visually distinguished — never a raise.

    ``placement`` is where the block sits inside the safe box — see :data:`PLACEMENTS`. The
    default is lower-third; ``center`` is accepted for face-free assets but lands on a presenter's
    mouth, which is why ``video_lint``'s V3 fails that combination on the finished file."""
    if ratio not in SAFE_AREAS:
        raise ValueError(f"unknown ratio {ratio!r} — expected one of {sorted(SAFE_AREAS)}")
    if placement not in PLACEMENTS:
        raise ValueError(f"unknown placement {placement!r} — expected one of {list(PLACEMENTS)}")
    if placement == "upper" and not upper_placement_fits(ratio):
        # Before 2026-08-28 this clamped to 1px and rendered anyway. Refusing is the point: the
        # caller has asked for a position that cannot exist at this ratio, and the silent version
        # of that produced 64 caption screens across a presenter's forehead.
        raise ValueError(
            f"placement='upper' has no room at {ratio}: the face band starts above the safe "
            "box, so no upper block clears a presenter's head. Use placement='lower' (or call "
            "resolve_placement(kit, ratio=...), which downgrades automatically)."
        )
    area = SAFE_AREAS[ratio]
    face = load_face(kit, repo_root=repo_root)
    box_x, box_y, box_w, box_h = _safe_box_px(area)
    accent_rgba = _resolve_accent_rgba(kit)
    glyph_rgba, stroke_rgba = _resolve_glyph_rgba(kit, backdrop_luma)
    emphasis_lower = {w.strip(".,!?;:\"'").lower() for w in emphasis}

    out_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[RenderedScreen] = []
    for i, screen in enumerate(screens):
        lines, font_px = fit(
            screen.text,
            area=area,
            face=face,
            max_height_px=_placement_height_budget(area, placement),
        )
        font = ImageFont.truetype(str(face), font_px)
        # Scales with type size so the outline reads the same at any fit — a fixed pixel stroke
        # is heavy on a short line (large font) and invisible on a long one (small font).
        stroke_w = max(2, round(font_px / 26))

        img = Image.new("RGBA", (area.width, area.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Scrim layer, composited UNDER the type. Colour choice alone cannot rescue a MID-TONE
        # backdrop: measured on this asset, a lamplit desk at luma ~0.20 gives 3.4:1 with cream
        # glyphs and no better inverted, because neither pole is 4.5:1 from the middle. The scrim
        # manufactures the pole. Drawn in the stroke tone (opposite the glyphs), rounded, blurred
        # so it reads as a soft vignette rather than a hard lower-third bar, and skipped entirely
        # when the caption is empty.
        scrim = Image.new("RGBA", (area.width, area.height), (0, 0, 0, 0))
        ascent, descent = font.getmetrics()
        line_height = (ascent + descent) * LINE_SPACING
        block_height = len(lines) * line_height
        block_top = _block_top(box_y, box_h, block_height, placement)

        widths = [draw.textlength(line, font=font) for line in lines]
        max_width = max(widths, default=0)
        block_left = box_x + (box_w - max_width) / 2  # horizontally centred

        pad_x, pad_y = round(font_px * 0.55), round(font_px * 0.34)
        ImageDraw.Draw(scrim).rounded_rectangle(
            [
                block_left - pad_x,
                block_top - pad_y,
                block_left + max_width + pad_x,
                block_top + block_height + pad_y,
            ],
            radius=round(font_px * 0.4),
            fill=(*stroke_rgba[:3], SCRIM_ALPHA),
        )
        img.alpha_composite(scrim.filter(ImageFilter.GaussianBlur(round(font_px * 0.18))))

        y = block_top
        for line, w in zip(lines, widths, strict=True):
            x = block_left + (max_width - w) / 2
            if emphasis_lower and accent_rgba is not None:
                _draw_line_with_emphasis(
                    draw,
                    line,
                    x,
                    y,
                    font=font,
                    emphasis_lower=emphasis_lower,
                    accent_rgba=accent_rgba,
                    glyph_rgba=glyph_rgba,
                    stroke_rgba=stroke_rgba,
                    stroke_w=stroke_w,
                )
            else:
                draw.text(
                    (x, y),
                    line,
                    font=font,
                    fill=glyph_rgba,
                    stroke_width=stroke_w,
                    stroke_fill=stroke_rgba,
                )
            y += line_height

        png_path = out_dir / f"caption_{i:02d}.png"
        img.save(png_path)

        rendered.append(
            RenderedScreen(
                screen=screen,
                png_path=png_path,
                font_px=font_px,
                lines=tuple(lines),
                box={
                    "x": round(block_left),
                    "y": round(block_top),
                    "w": round(max_width),
                    "h": round(block_height),
                },
                font_path=face,
            )
        )
    return rendered


def overlay_filtergraph(rendered: list[RenderedScreen]) -> str:
    """Build the ffmpeg filter_complex fragment overlaying each caption PNG onto input 0, in
    order. Assumes each PNG is added as a sequential ffmpeg -i input starting at index 1.

    Each caption PNG is a FULL-FRAME-sized transparent canvas (render() draws the text at its
    already-computed absolute position within that canvas) — the overlay itself must therefore
    sit at (0, 0). Overlaying at ``r.box['x']``/``r.box['y']`` would double-apply the offset
    (once baked into the PNG, once again by the overlay filter), pushing the caption further
    right/down each render and, for a wide line, off the right edge of the frame entirely."""
    if not rendered:
        return ""
    label = "0:v"
    parts = []
    for i, r in enumerate(rendered, start=1):
        out_label = f"vcap{i}" if i < len(rendered) else "vout"
        enable = f"between(t,{r.screen.start_s},{r.screen.end_s})"
        parts.append(f"[{label}][{i}:v]overlay=x=0:y=0:enable='{enable}'[{out_label}]")
        label = out_label
    return ";".join(parts)


def sidecar_payload(rendered: list[RenderedScreen], *, ratio: str) -> dict:
    """The captions.json-shaped payload — the geometry contract gtm_core.video_lint's V3 checks
    against. Factored out of :func:`write_sidecar` so :mod:`gtm_core.video_finish` can embed the
    identical payload into finish-<ratio>.json (render_manifest.FinishManifest.captions) without
    a second render pass or a second file read."""
    area = SAFE_AREAS[ratio]
    font_path = None
    font_sha256 = None
    if rendered:
        font_path = str(rendered[0].font_path)
        font_sha256 = hashlib.sha256(rendered[0].font_path.read_bytes()).hexdigest()

    return {
        "frame": [area.width, area.height],
        "safe_area": {
            "left": area.left,
            "right": area.right,
            "top": area.top,
            "bottom": area.bottom,
        },
        "font_path": font_path,
        "font_sha256": font_sha256,
        "screens": [
            {
                "index": i,
                "text": r.screen.text,
                "start_s": r.screen.start_s,
                "end_s": r.screen.end_s,
                "estimated": r.screen.estimated,
                "font_px": r.font_px,
                "lines": list(r.lines),
                "box": r.box,
                "png": str(r.png_path),
            }
            for i, r in enumerate(rendered)
        ],
    }


def write_sidecar(rendered: list[RenderedScreen], *, ratio: str, out_dir: Path) -> Path:
    """Write captions.json — the geometry contract gtm_core.video_lint's V3 checks against."""
    payload = sidecar_payload(rendered, ratio=ratio)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "captions.json"
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return out_path
