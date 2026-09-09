"""Brand-neutral design tokens — the SCALE every generated surface lays out against.

A PORT, NOT AN INVENTION
------------------------
Every value below already existed, in CSS, at ``docker/deck-renderer/deck-theme/styles/``:
``tokens.css`` (the brand-neutral core), ``animations.css`` (the stagger helper and the reveal
vocabulary) and the ``[data-tempo]`` map in ``slidev-overrides.css``. The deck and carousel
surfaces have run on it since 2026-08-29. The video surface — ``gtm_core.screen_ui`` — never
received it, which is why its corner radii ran 3.8x apart for panels doing the same job, its
stroke widths were bare integers while every other dimension scaled with the frame, and half its
cards revealed by opacity alone while the other half moved. This module is that CSS expressed as
Python so a Pillow renderer can consume the same scale a stylesheet does.

THE ONE REAL TRANSLATION: rem -> fraction of FRAME HEIGHT
---------------------------------------------------------
The deck version is ``rem``-based against Slidev's fixed 980x551 design canvas, which is then
scaled to the viewport. A video card is rendered at 1920x1080, 1080x1350 or 1080x1920, so a token
fixed in pixels would be three different sizes relative to the frame. Every token here is
therefore a FRACTION OF FRAME HEIGHT, converted once through the deck's own canvas
(``_REM_H = 16px / 551px``), so one token means the same proportion of the frame at 16:9, 4:5 and
9:16 alike. Call ``px(frac, h)`` to land it.

WHY THE SCALE IS HERE AND NOT IN THE TENANT'S BRAND KIT
-------------------------------------------------------
A spacing ladder is not a tenant fact. Putting one in ``profiles/<tenant>/knowledge/BRAND.toml``
would repeat the de-brand violation ``screen_ui/palette.py`` was fixed for on 2026-09-01 (one
tenant's navy and teal sitting in engine code, rendering every OTHER tenant in that tenant's
colours), and it would promote repo-invented numbers into a file whose own authority line says it
"derives from, and is subordinate to" the brand team's document — a document that defines colour,
type, imagery and marks, and says nothing about cubic-beziers. The split this module keeps is the
one the deck theme already models: THE SCALE IS AN ENGINE CONVENTION, THE CHOICE IS A TENANT
FACT. ``resolve_motion`` / ``resolve_layout`` read that choice — which tempo is the default,
whether the focal element glows, how dense the layout is — from the kit's ``[motion]`` and
``[layout]`` blocks, and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass

# ── the rem -> frame-height conversion, done once ───────────────────────────────────────────────
#
# Slidev's design canvas is 980x551 and the theme's root font size is the browser default 16px.
# Every `rem` value ported below is divided through by that canvas height, so `--space-md: 1rem`
# becomes "2.9% of frame height" rather than "16 pixels" — the same proportion of the frame at
# every ratio this module's consumers render.
_REM_PX = 16.0
_DECK_CANVAS_H = 551.0
_REM_H = _REM_PX / _DECK_CANVAS_H  # 0.029038...


def rem(value: float) -> float:
    """``value`` rem, as a fraction of frame height. The port's one unit conversion."""
    return value * _REM_H


def px(frac: float, h: int, *, minimum: int = 0) -> int:
    """A height-fraction token landed on a frame ``h`` pixels tall, never below ``minimum``."""
    return max(minimum, round(frac * h))


# ── spacing (Material 3 dialect — tokens.css "Spacing") ─────────────────────────────────────────
SPACE: dict[str, float] = {
    "2xs": rem(0.25),
    "xs": rem(0.5),
    "sm": rem(0.75),
    "md": rem(1.0),
    "lg": rem(1.5),
    "xl": rem(2.0),
    "2xl": rem(2.5),
    "3xl": rem(3.0),
    "4xl": rem(4.0),
    "5xl": rem(5.0),
    "6xl": rem(6.0),
    "7xl": rem(8.0),
}

#: Semantic axes over SPACE, exactly as tokens.css defines them: a consumer names its INTENT
#: (symmetric padding / vertical gap / horizontal gap) rather than a number, so a layout can be
#: re-tuned globally without editing every scene. `INSET` is padding inside a panel, `STACK` is
#: the gap between stacked siblings, `INLINE` the gap between side-by-side ones.
INSET: dict[str, float] = {k: SPACE[k] for k in ("xs", "sm", "md", "lg", "xl", "2xl", "3xl")}
STACK: dict[str, float] = {k: SPACE[k] for k in ("xs", "sm", "md", "lg", "xl", "2xl", "3xl")}
INLINE: dict[str, float] = {k: SPACE[k] for k in ("xs", "sm", "md", "lg", "xl")}

# ── radii (tokens.css "Radii"; --radius-full is not portable to a Pillow box) ───────────────────
RADIUS: dict[str, float] = {
    "xs": rem(0.25),
    "sm": rem(0.5),
    "md": rem(0.75),
    "lg": rem(1.0),
    "xl": rem(1.5),
    "2xl": rem(2.0),
}

# ── strokes — the ONE token family with no CSS ancestor ────────────────────────────────────────
#
# The deck theme writes `border: 1px` / `2px` in component CSS, which is fine on a fixed canvas
# the browser scales. `screen_ui` draws into a real pixel grid at three different heights, and it
# was passing bare integers (`width=2`, `width=3`, `width=4`) while every other dimension in the
# same call scaled with `h` — `caller_record.py` did both in one scene. These are h-relative so a
# rule is the same weight relative to the type beside it at every ratio, with a 1px floor because
# a sub-pixel line does not exist.
#
# Ported at the deck's own scale, which means every rule roughly DOUBLES against what `screen_ui`
# shipped: a 1px CSS border on the 551-tall canvas is ~2 device px once Slidev scales it to a
# 1080-tall viewport. That is the correction, not a side effect. Deck body type is 1.05rem
# (0.0305h) against a 2px border; `screen_ui` payload type sits at 0.038h against a 2px-at-1080
# border, i.e. HALF the deck's proportional line weight for LARGER type. Thin rules under heavy
# type is most of what "the lines felt off" was.
STROKE: dict[str, float] = {
    "thread": 0.5 / _DECK_CANVAS_H,  # ~1px at 1080 — the backdrop lattice, at the edge of
    #                                  perception by design; not a rule anyone is meant to read
    "hairline": 1.0 / _DECK_CANVAS_H,  # ~2px at 1080 — a divider inside a panel
    "rule": 2.0 / _DECK_CANVAS_H,  # ~4px at 1080 — a panel border
    "emphasis": 3.0 / _DECK_CANVAS_H,  # ~6px at 1080 — a live edge, a connector
    "heavy": 4.0 / _DECK_CANVAS_H,  # ~8px at 1080 — a strike-through, a spine
}


def stroke_px(name: str, h: int) -> int:
    """A named stroke weight in whole pixels on a frame ``h`` tall. Never returns 0."""
    return px(STROKE[name], h, minimum=1)


# ── motion: easing ──────────────────────────────────────────────────────────────────────────────
#
# The five cubic-beziers from tokens.css "Motion", solved numerically. `entrance` is the one the
# reveal vocabulary in animations.css actually uses; the other four are ported so a later consumer
# does not reinvent them with different numbers.
EASINGS: dict[str, tuple[float, float, float, float]] = {
    "standard": (0.2, 0.0, 0.0, 1.0),
    "emphasized": (0.0, 0.0, 0.0, 1.0),
    "emphasized_decelerate": (0.05, 0.7, 0.1, 1.0),
    "emphasized_accelerate": (0.3, 0.0, 0.8, 0.15),
    "entrance": (0.22, 1.0, 0.36, 1.0),
}


def _bezier_axis(t: float, a: float, b: float) -> float:
    """One axis of a cubic Bezier with endpoints pinned at 0 and 1."""
    u = 1.0 - t
    return 3 * u * u * t * a + 3 * u * t * t * b + t * t * t


def ease(name: str, t: float) -> float:
    """A CSS ``cubic-bezier`` evaluated at progress ``t`` in [0, 1].

    CSS beziers are y-as-a-function-of-x, so x must be solved for first. Newton converges on every
    curve in ``EASINGS`` (all are monotone in x); the bisection tail is the guard for a
    hypothetical curve added later with a near-zero derivative, so this can never spin or return a
    wrong branch. Deterministic to the last bit — two runs of the same scene match frame for
    frame, which is the property the whole ``screen_ui`` module is built on.
    """
    t = max(0.0, min(1.0, t))
    x1, y1, x2, y2 = EASINGS[name]
    guess = t
    for _ in range(8):
        err = _bezier_axis(guess, x1, x2) - t
        if abs(err) < 1e-6:
            break
        u = 1.0 - guess
        d = 3 * u * u * x1 + 6 * u * guess * (x2 - x1) + 3 * guess * guess * (1 - x2)
        if abs(d) < 1e-6:
            break
        guess = max(0.0, min(1.0, guess - err / d))
    else:
        lo, hi = 0.0, 1.0
        for _ in range(24):
            guess = (lo + hi) / 2
            if _bezier_axis(guess, x1, x2) < t:
                lo = guess
            else:
                hi = guess
    return _bezier_axis(guess, y1, y2)


# ── motion: tempo ───────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Tempo:
    """One rhythm, as data. Ported verbatim from the ``[data-tempo]`` map.

    ``travel`` is a fraction of frame height (the CSS is px on the 551-tall canvas). A consumer
    names a tempo; it never names a duration — that is the whole point of the vocabulary.
    """

    name: str
    stagger_s: float
    duration_s: float
    travel: float


#: The three named rhythms. A fourth is a design decision, not a convenience: the vocabulary is
#: small on purpose so an author picks a rhythm rather than dialling a number.
#:
#: DRIFT FIXED IN THE PORT: tokens.css sets a base `--tempo-stagger: 60ms` commented "brisk — the
#: default" while the `[data-tempo="brisk"]` block that actually applies sets 55ms. Duration and
#: travel agree across the two; only the stagger disagreed. 55ms is the one that ever reached a
#: rendered deck, so 55ms is what is ported. The 60ms base is a stale default, not a second
#: opinion — do not port both.
TEMPOS: dict[str, Tempo] = {
    "hold": Tempo("hold", 0.090, 0.700, 40.0 / _DECK_CANVAS_H),
    "brisk": Tempo("brisk", 0.055, 0.420, 24.0 / _DECK_CANVAS_H),
    "burst": Tempo("burst", 0.030, 0.240, 14.0 / _DECK_CANVAS_H),
}

#: Beyond this index every sibling arrives together. animations.css clamps at 7 (`nth-child(1..7)`
#: take 0..6, everything from the 8th on takes 7): "12 children at 100ms is a 1.1s cascade, past
#: the point an audience reads it as one gesture rather than as a queue". At the brisk step that
#: caps a cascade at 7 * 55ms = 385ms no matter how long the list is.
STAGGER_INDEX_CLAMP = 7


def stagger_index(i: int) -> int:
    """The clamped cascade position of the ``i``-th sibling (0-based)."""
    return min(max(0, int(i)), STAGGER_INDEX_CLAMP)


#: The reveal VOCABULARY, ported from the keyframes in animations.css:
#: ``(travel_scale, duration_scale, scale_from)`` per class.
#:
#: Read the keyframes, not the class names — that is the difference between a port and a guess.
#: ``fade-up`` is the only one that TRANSLATES (``translateY(var(--anim-travel))``); ``soft-fade``
#: is opacity alone; ``scale-in`` grows from 0.96 and does not move. The first draft of this table
#: gave all three a travel and it was wrong twice over: it invented motion the deck surface does
#: not have, and on this surface a row's entrance then reached down into the band a caption is
#: burned into, which ``tests/media/test_screen_ui.py`` caught.
#:
#: The duration scales ARE verbatim — soft-fade 0.85, wipe 1.35, mask-in 1.35, collapse 1.2 — and
#: exist because duration must scale with distance, or a short move and a long one read at
#: different velocities. A scene names a class; it never names a distance.
REVEAL_CLASSES: dict[str, tuple[float, float, float]] = {
    "fade_up": (1.00, 1.00, 1.0),  # the workhorse — a panel, a card, a node
    "soft_fade": (0.00, 0.85, 1.0),  # a row or subtitle inside something already arrived
    "scale_in": (0.00, 1.00, 0.96),  # a chip, a tag, a system box
    "wipe": (1.60, 1.35, 1.0),  # a track or connector that draws itself across
    "mask_in": (1.20, 1.35, 1.0),  # imagery and full-bleed panels
    "collapse": (0.80, 1.20, 1.0),  # something folding into place
}


@dataclass(frozen=True)
class Reveal:
    """One element's entrance, in fractions of SHOT progress plus a pixel travel distance.

    ``screen_ui`` scenes are driven by a single ``t`` in [0, 1] across the shot, so a tempo
    expressed in seconds has to be divided through by the shot's own duration before a scene can
    use it. That division happens once, here, rather than in every scene.
    """

    start: float
    end: float
    travel_px: int
    scale_from: float = 1.0

    def progress(self, t: float, *, easing: str = "entrance") -> float:
        """Eased [0, 1] arrival of this element at shot progress ``t``."""
        if self.end <= self.start:
            return 1.0 if t >= self.end else 0.0
        return ease(easing, (t - self.start) / (self.end - self.start))

    def offset_px(self, t: float, *, easing: str = "entrance") -> int:
        """Remaining travel, in pixels, at shot progress ``t``. 0 once arrived."""
        return round(self.travel_px * (1.0 - self.progress(t, easing=easing)))

    def scale(self, t: float, *, easing: str = "entrance") -> float:
        """Current scale factor, for the classes that GROW rather than move. 1.0 once arrived."""
        return self.scale_from + (1.0 - self.scale_from) * self.progress(t, easing=easing)


def reveal(
    index: int,
    *,
    tempo: Tempo,
    duration_s: float,
    h: int,
    start_s: float = 0.0,
    duration_scale: float = 1.0,
    travel_scale: float = 1.0,
    scale_from: float = 1.0,
    reduced_motion: bool = False,
) -> Reveal:
    """The ``index``-th element's entrance window and travel distance.

    ``duration_scale`` is ``--anim-duration-scale`` from tokens.css: duration scales WITH
    distance, because the eye judges velocity, not duration. A 24px fade-up and a 60px slide-in
    given the same 600ms read at wildly different speeds — which is exactly what every card in
    this film did, one window for everything regardless of what moved in it. Pass 1.35 for a
    reveal that travels the full width, 0.85 for a soft fade that barely moves.

    ``reduced_motion`` collapses travel, stagger AND duration together. Note that this is what
    animations.css actually does, and its comment says why zeroing only the duration is wrong:
    "zeroing only the duration leaves the DELAYS intact, so a staggered cascade still arrives as a
    sequence of pops — slower, and arguably worse, than the animation it replaced. Everything
    lands at once instead."
    """
    if duration_s <= 0:
        return Reveal(0.0, 0.0, 0)
    if reduced_motion:
        start = max(0.0, min(1.0, start_s / duration_s))
        return Reveal(start, min(1.0, start + 1e-4), 0)
    delay_s = stagger_index(index) * tempo.stagger_s
    span_s = tempo.duration_s * max(0.0, duration_scale)
    start = (start_s + delay_s) / duration_s
    end = (start_s + delay_s + span_s) / duration_s
    travel_px = px(tempo.travel * max(0.0, travel_scale), h)
    return Reveal(max(0.0, min(1.0, start)), max(0.0, min(1.0, end)), travel_px, scale_from)


# ── the tenant's CHOICES, read from the kit ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Motion:
    """What a tenant chooses about rhythm. Never the scale itself — see the module docstring."""

    default_tempo: Tempo
    glow_on_focal: bool
    reduced_motion: bool


@dataclass(frozen=True)
class Layout:
    """What a tenant chooses about density and fill."""

    density: str
    density_scale: float
    safe_box_fill_min: float
    safe_box_fill_max: float


#: Density multiplies the SPACING ladder only — never radii, never strokes, never motion. A denser
#: layout is one that fits more in; it is not one with sharper corners or faster entrances.
_DENSITY_SCALE = {"comfortable": 1.0, "compact": 0.8, "spacious": 1.2}


def _block(kit: object, name: str) -> dict:
    block = kit.get(name) if isinstance(kit, dict) else None
    return block if isinstance(block, dict) else {}


def resolve_motion(kit: dict) -> Motion:
    """Read ``[motion]`` from a resolved brand kit, degrading to the engine default.

    A kit with no ``[motion]`` block renders on ``brisk`` — the same posture ``_load_palette``
    takes on a thin kit and ``captions.py`` takes on a missing ``[disclosure]``. A missing
    nice-to-have degrades; it never raises.
    """
    m = _block(kit, "motion")
    name = str(m.get("default_tempo") or "brisk")
    return Motion(
        default_tempo=TEMPOS.get(name, TEMPOS["brisk"]),
        glow_on_focal=bool(m.get("glow_on_focal", True)),
        reduced_motion=bool(m.get("reduced_motion", False)),
    )


def resolve_layout(kit: dict) -> Layout:
    """Read ``[layout]`` from a resolved brand kit, degrading to the engine default."""
    layout = _block(kit, "layout")
    density = str(layout.get("density") or "comfortable")
    return Layout(
        density=density,
        density_scale=_DENSITY_SCALE.get(density, 1.0),
        safe_box_fill_min=float(layout.get("safe_box_fill_min", 0.90)),
        safe_box_fill_max=float(layout.get("safe_box_fill_max", 1.00)),
    )
