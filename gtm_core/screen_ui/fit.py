from __future__ import annotations

from pathlib import Path

from PIL import ImageDraw, ImageFont

from .base import SceneError
from .draw import _LABEL_TRACKING, _fit_font, _font, _text_w, _tracked_w

#: When not None, :func:`_fit_or_refuse` records every fit it performs here. Off by default and
#: costing nothing; :func:`audit_fit` turns it on for the duration of a render. A module-level
#: collector rather than a return value because the callers are per-frame draw loops and threading
#: a report out of them would change every signature to answer a question they do not ask.
_FIT_LOG: list[dict] | None = None


def fit_tolerance(width_at_floor: float, box_w: float) -> float:
    """How much WIDER a face could be before this string stops fitting: ``box / width_at_floor``.

    THE METRIC THAT WAS MISSING, and the reason "it fits" was never the right question. Clearance
    ("measured is 3% under the box") looks like a safety margin and is not one: `_fit_font` stops
    at the FIRST size that fits, so every shrink-to-fit string lands ~1% under its box by
    construction, whether it had room to spare or was one step from the floor. Clearance therefore
    reports the search's stopping rule, not the layout's headroom.

    Width at the FLOOR against the box is the honest number, because the floor is the one size the
    search may not go below. 1.00 means the string only just survives at the floor; 1.05 means a
    face 5% wider refuses it. Real sans faces vary by ~20% in advance width, so anything under
    about 1.15 is holding by luck rather than by design — which is exactly what the record card's
    value line turned out to be doing, at 1.03, until a wider test face made it audible.
    """
    return box_w / width_at_floor if width_at_floor > 0 else float("inf")


def _fit_or_refuse(
    draw: ImageDraw.ImageDraw,
    face: Path,
    text: str,
    max_w: int,
    start_px: int,
    floor_px: int,
    *,
    where: str,
) -> ImageFont.FreeTypeFont:
    """``_fit_font``, but a string that will not fit even at the floor is a build failure.

    `_fit_font` returns its floor size when the floor still overruns, so a caller gets a font
    back either way and the overflow only appears in the rendered pixels. That is how the flow
    card shipped on 2026-08-30 with "one checkpoint, in front of your systems" hanging 51px past
    both edges of the panel it labels — a ui-craft absolute ban (text overflowing its container),
    drawn every frame, past a clean video_lint, and caught only by looking at a frame.

    Refusing is the right behaviour: the fix is always to shorten the string or widen the box,
    and both are decisions a person has to make. A renderer silently drawing outside its own
    geometry cannot be reviewed, because the artefact looks like a choice.
    """
    font = _fit_font(draw, face, text, max_w, start_px, floor_px)
    got = _text_w(draw, text, font)
    if _FIT_LOG is not None:
        _FIT_LOG.append(
            {
                "where": where,
                "text": text,
                "box_w": int(max_w),
                "resolved_px": font.size,
                "floor_px": int(floor_px),
                "at_floor": font.size <= floor_px,
                "width_at_floor_px": round(_text_w(draw, text, _font(face, int(floor_px))), 1),
            }
        )
    if got > max_w:
        raise SceneError(
            f"{where}: {text!r} measures {got:.0f}px at its {font.size}px floor, "
            f"inside a {max_w}px box — shorten the copy or widen the panel"
        )
    return font


def _fit_trial(
    draw: ImageDraw.ImageDraw,
    face: Path,
    text: str,
    max_w: int,
    start_px: int,
    floor_px: int,
) -> tuple[ImageFont.FreeTypeFont, bool]:
    """Fit, and SAY whether it fit — the one legitimate shape that is neither
    :func:`_fit_or_refuse` nor a defect.

    A caller escalating through candidate layouts (a narrower left margin, then a lower floor)
    NEEDS the first candidate to be allowed to overflow: overflowing is the signal to try the
    next one. :func:`_fit_or_refuse` cannot express that, and calling :func:`_fit_font` bare
    cannot either — it returns a font and says nothing, which is exactly the silence that shipped
    a sub-line 51px outside its panel.

    So the search gets its own verb. The caller must still refuse when every candidate fails;
    this returns the answer, it does not act on it.
    """
    font = _fit_font(draw, face, text, max_w, start_px, floor_px)
    return font, _text_w(draw, text, font) <= max_w


def _fit_tracked(
    draw: ImageDraw.ImageDraw,
    face: Path,
    text: str,
    max_w: int,
    start_px: int,
    floor_px: int,
    *,
    tracking: float = _LABEL_TRACKING,
) -> ImageFont.FreeTypeFont:
    """``_fit_font`` for text that will be drawn TRACKED.

    Tracking adds width that `_fit_font` cannot see, so sizing with one and drawing with the other
    overruns the container by exactly the accumulated letter-spacing — which is how three node
    labels came to hang outside their own panels on the first design pass. Fitting and drawing
    must measure the same string the same way.

    KNOWN GAP, STATED SO NOBODY ASSUMES IT IS COVERED: this reimplements the search rather than
    wrapping :func:`_fit_font` (it has to — it measures with :func:`_tracked_w`), and like
    :func:`_fit_font` it RETURNS ITS FLOOR when even the floor overruns instead of refusing. It
    has no `_fit_or_refuse` twin. That is tolerable only because every caller passes a short
    tracked LABEL rather than a sentence, so the floor has never been reached in practice — it is
    not a guarantee, and `test_no_scene_renderer_calls_fit_font_bare` does not cover this path.
    Give it a refusing twin before the first long tracked string, not after.
    """
    size = start_px
    while size > floor_px:
        font = _font(face, size)
        if _tracked_w(draw, text, font, tracking=tracking) <= max_w:
            return font
        size -= 2
    return _font(face, floor_px)


# ── caller-record — a record being read and then written, one variant per call ─────────────────
#
# WHY A SCENE AND NOT A COMPOSITE. The first build of this beat rendered the record as an HTML
# card and perspective-warped it onto a laptop screen inside a photoreal plate, to keep the
# graphic "in the room". Two defects followed, both structural rather than fixable by tuning:
# the screen occupied ~14% of the frame's width, so the payload text was illegible at feed size
# — the exact failure the type floor below exists to prevent — and recovering legibility meant
# cropping into the plate and upscaling ~5x, which is where the visible pixelation came from. A
# warp can only ever RESAMPLE DOWN without softening, so the record and the room cannot both be
# large in one frame. The fix is film grammar, not compositing: the plate is the establishing
# shot, this is the insert, and they are cut together. Every pixel here is drawn at the delivery
# ratio's native size and never resampled.

#: Minimum height fraction for text carrying PAYLOAD (a value, a name, an answer) — as opposed to
#: an overline or a short all-caps badge, which stay legible smaller because they are tracked and
#: pre-read from context. At 16:9 this is 0.050 * 1080 = 54px. The arithmetic it comes from: a
#: 1920-wide master in a phone feed lays out at ~390pt, so one source pixel is ~0.203pt and 54px
#: renders at ~11pt — the floor for comfortable body text. Type chosen against a 27" display and
#: never checked against that number is how the first cut of this film came back unreadable.
_PAYLOAD_MIN_H_FRAC = 0.050

#: Floor for SECONDARY type — captions, notes, attributions. Lower than the payload floor because
#: these lines support a statement rather than carrying it, but still a floor: the 2026-08-29 cut
#: set card sub-lines at ~0.030 of height, which is ~6pt once a 1920 master is laid out at phone
#: width, and the operator could not read them ("clearer?", 2026-08-30). 0.038 lands at ~8.3pt,
#: which is small-but-legible on a feed. Nothing that a viewer must READ may go below this.
_SECONDARY_MIN_H_FRAC = 0.038

#: The one element permitted below :data:`_SECONDARY_MIN_H_FRAC`, and only because of what it is.
#: A footnote here is a VERBATIM CITATION — `_ROWS_LAYERS` says so in its own comment ("Verbatim
#: and unaltered — fewer quotes, never edited ones"), and a quotation cannot be shortened to fit a
#: box without changing what was quoted. Every other lever is closed: the footer already sits
#: BELOW the burned caption band, so it cannot move up (that is the overprint
#: `test_no_scene_draws_text_into_the_burned_caption_band` exists to refuse), and at 16:9 the band
#: between `_FOOT_BAND_TOP_FRAC` and the safe-area floor holds exactly two lines, so the quote
#: cannot wrap either. Measured 2026-08-31: the IMDA §2.1.2 fragment needs 1620px of a 1594px box
#: at 0.038h — 1.6% over, invisible until `_fit_or_refuse` was wired in, and clipped in the
#: delivered card. 0.033h clears it with margin on both the brand face and a wider substitute.
#: This is a floor for CITATIONS, not a general relaxation: raise the quote, not this number.
#:
#: THE OBVIOUS ALTERNATIVE IS WORSE, measured so nobody re-tries it. Dropping the surrounding
#: curly quote marks (presentation, not the quoted words, and the attribution beneath already
#: marks it as a citation) lets the fragment fit at the full 0.038h floor — but only just: 1581px
#: in a 1594px box, tolerance x1.008. Shrinking the type instead lands at x1.107. Two typographic
#: points of size buys ten times the headroom, so the type yields and the quote is left alone.
_CITATION_MIN_H_FRAC = 0.033
