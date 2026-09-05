from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ...captions import FontMissing, load_face, load_faces
from ...design_tokens import stroke_px
from ..base import SceneError
from ..draw import (
    CAPTION_BAND_TOP_FRAC,
    _backdrop,
    _ease_in_out,
    _font,
    _lerp_color,
    _text_w,
    _window,
)
from ..fit import _fit_or_refuse
from ..frames import _frame_count, _write_frames
from ..palette import _hex_to_rgb, _load_palette, _paste_logo, _prepare_logo, _resolve_area

# ── title-card — the film's spine line, and its reprise ────────────────────────────────────────


@dataclass(frozen=True)
class _TitleSpec:
    prefix: str
    line_one: str
    line_two: str
    with_disclosure: bool = False
    #: Whether this card carries a CTA at all — never the URL itself. The URL is a tenant fact
    #: and belongs in the kit (``[cta].url``), resolved at render time by
    #: ``render_title_card_frames``, the same way the disclosure line is read from
    #: ``[disclosure].line`` rather than written here. A literal here is exactly the bug this
    #: replaced: every tenant's close card said "nexus.com" until 2026-09-02, because the string
    #: was hardcoded on this dataclass instead of resolved per profile.
    has_cta: bool = False


#: The film's spine line, said once at 0:40 and reprised over the disclosure at the end. It
#: replaced "A name is a claim. / It isn't proof." on 2026-08-30: nobody believes a name is
#: proof, so that framing argued with a position no viewer holds. The point is not that people
#: are fooled — every caller in Act 1 is honest — it is that nothing in the system required the
#: truth. State the requirement and the reason instead.
_SPINE_ONE = "Every caller has to be verified."
_SPINE_TWO = "Everything after that depends on it."

_TITLE_CLAIM = _TitleSpec("title-claim", _SPINE_ONE, _SPINE_TWO)
_TITLE_CLOSE = _TitleSpec("title-close", _SPINE_ONE, _SPINE_TWO, with_disclosure=True, has_cta=True)


# (``CAPTION_BAND_TOP_FRAC`` and ``_text_band`` now live in the design-system section above, so
# every scene shares one bottom edge instead of each keeping its own.)


#: The disclosure is the one string on this card drawn at a FIXED size rather than fitted, because
#: its size is a legibility duty rather than a design choice. That was safe while the card only ran
#: 16:9, where a 60-character line at 0.040h clears a 1730px box with room to spare. At 9:16 the box
#: narrows to ~864px while the type scales UP with height, and the line ran off BOTH edges of the
#: frame — an Art. 50 disclosure that is not merely small but partly absent. Wrapping keeps the size
#: (and so the duty) and spends the height the portrait frame has instead.
def _wrap_px(
    text: str, font: ImageFont.FreeTypeFont, max_w: int, draw: ImageDraw.ImageDraw
) -> list[str]:
    lines: list[str] = []
    current: list[str] = []
    for word in text.split():
        if current and draw.textlength(" ".join([*current, word]), font=font) > max_w:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def _apply_title_override(spec: _TitleSpec, title: dict[str, str] | None) -> _TitleSpec:
    """``spec`` with ``line_one``/``line_two`` replaced where the caller supplied them.

    An unknown key raises rather than being dropped: a typo'd key would otherwise ship the built-in
    spine as if it were the film's own line, which is the failure the override exists to prevent.
    """
    if not title:
        return spec
    allowed = {"line_one", "line_two"}
    unknown = sorted(set(title) - allowed)
    if unknown:
        raise SceneError(
            f"title card: unknown key(s) {unknown} — expected one of {sorted(allowed)}"
        )
    fields = {}
    for key, value in title.items():
        text = str(value).strip()
        # `line_two=""` is a REQUEST, not a mistake: a name-only end card. `line_one=""` is not —
        # a card with no first line is a card with nothing on it.
        if not text and key != "line_two":
            raise SceneError(f"title card: {key} was set to an empty string")
        fields[key] = text
    return replace(spec, **fields)


def render_title_card_frames(
    *,
    kit: dict,
    ratio: str,
    fps: int,
    duration_s: float,
    out_dir: Path,
    spec: _TitleSpec = _TITLE_CLAIM,
    font_role: str = "caption",
    logo: bool = False,
    logo_variant: str = "horizontal",
    title: dict[str, str] | None = None,
    cta_text: str | None = None,
    repo_root: Path | None = None,
) -> int:
    """Two lines of type, the second landing after the first has been read.

    The reprise variant fades the tenant's disclosure line up beneath them. That line is read from
    the kit, never written here: ``[disclosure].line`` is the tenant's own wording and a tenant who
    has not configured one has not opted out of the duty — a hardcoded default would paper over
    exactly the fail-closed check ``validate_disclosure`` performs at the publish gate.

    ``logo=True`` composites the brand mark top-left, background-matched via ``[assets]``. Default
    ``False``, and title-claim/title-close are the only scenes wired to accept it at all — see the
    "logo compositing" section above ``_load_palette`` for why every other scene refuses it.

    ``logo_variant`` picks which ``[assets].logo_<variant>_<background>`` entry is composited.
    Defaults to the horizontal lockup, which is what a wordmark-carrying brand wants; a brand whose
    end card is its SYMBOL (and which therefore vendors no horizontal lockup) passes ``"symbol"``.
    Stated by the caller rather than inferred from which keys the kit happens to hold — falling
    back from a missing lockup to a symbol would silently change the design of the card.

    ``title={"line_one": ..., "line_two": ...}`` overrides the spine for a film that is not the one
    :data:`_SPINE_ONE` was written for. The literals below are ONE film's argument sitting in engine
    code — the same shape as the hardcoded CTA URL this module already had to unlearn, and it fails
    the same silent way: a second film calling ``title-close`` gets the first film's sentences,
    rendered perfectly, with nothing to notice. Unset keys keep the spec, so every existing caller
    is byte-identical.
    """
    area = _resolve_area(ratio)
    spec = _apply_title_override(spec, title)
    palette = _load_palette(kit)
    try:
        faces = load_faces(kit, repo_root=repo_root)
        face = (
            faces.caption
            if font_role == "caption"
            else load_face(kit, role=font_role, repo_root=repo_root)
        )
    except FontMissing as exc:
        raise SceneError(str(exc)) from exc

    # PER-STRING font roles, not one face for the whole card. The kit's `[typography].display_use`
    # is explicit that the display face is for "hero headline only — the single main
    # campaign/page/slide headline, never body, labels, captions, tables, charts or UI text", so a
    # card-wide `--font-role display` would set the Art. 50 disclosure in it and violate the kit
    # while appearing to honour it. Only the two spine lines get the hero face; the CTA and the
    # disclosure stay on `face`.
    #
    # OPTIONAL, and it degrades rather than raising: a kit that vendors no display face renders
    # the spine in its body face, which is what a single-face tenant should get. Same posture as
    # `load_faces` falling back to `caption` for an omitted weight role.
    try:
        hero_face = load_face(kit, role="display", repo_root=repo_root)
    except FontMissing:
        hero_face = face

    canvas_rgb = _hex_to_rgb(palette.canvas, field="canvas")
    text_rgb = _hex_to_rgb(palette.text, field="text")
    dim_rgb = _hex_to_rgb(palette.text_dim, field="text_dim")
    accent_rgb = _hex_to_rgb(palette.accent, field="accent")

    w, h = area.width, area.height
    max_w = w - 2 * round(w * area.left) - 2 * round(w * 0.04)
    line_px = round(h * 0.115)
    floor_px = round(h * 0.070)
    disc_px = round(h * 0.040)

    disclosure = ""
    if spec.with_disclosure:
        block = kit.get("disclosure") if isinstance(kit, dict) else None
        disclosure = (block or {}).get("line", "") if isinstance(block, dict) else ""

    # Same resolve-from-kit shape as `disclosure` just above: a tenant with no `[cta].url`
    # configured gets no CTA element at all (the schedule below already handles `cta_text == ""`
    # as "this card has four elements, not five"), never a placeholder domain that isn't theirs.
    #: The kit stays the source for a tenant's URL — that is a tenant fact and hardcoding it here
    #: is the bug this whole block replaced. A caller may override it because not every card's call
    #: to action IS a URL: a film whose ask is "free on the App Store" has a line that belongs to
    #: the film, not to the profile, and burning it as a caption instead puts the one frame that
    #: asks for something in subtitle styling.
    if cta_text is None:
        cta_text = ""
        if spec.has_cta:
            block = kit.get("cta") if isinstance(kit, dict) else None
            cta_text = (block or {}).get("url", "") if isinstance(block, dict) else ""

    _probe = ImageDraw.Draw(Image.new("RGB", (w, h)))
    disc_font = _font(face, disc_px)
    disc_lines = _wrap_px(disclosure, disc_font, max_w, _probe) if disclosure else []
    disc_line_h = round(disc_px * 1.28)
    disc_block_h = disc_line_h * len(disc_lines)

    n_frames = _frame_count(fps, duration_s)
    ONE_START, ONE_END = 0.04, 0.20
    TWO_START, TWO_END = 0.38, 0.54
    RULE_START, RULE_END = 0.54, 0.70
    CTA_START, CTA_END = 0.70, 0.70  # inert unless the card carries a CTA
    DISC_START, DISC_END = 0.70, 0.90

    #: A purely proportional schedule gives the Art. 50 disclosure whatever time the card happens
    #: to have left, which is not a property anyone chose. Measured on the 2026-08-30 cut: the
    #: closing card ran 6.16s (its VO's length), so 0.70..0.90 put the line fully up at 5.54s and
    #: held it for 1.24s before the film cut to black — counted in frames, not estimated. The
    #: disclosure is the one element on this card with a legal reason to be legible, so the HOLD is
    #: specified in seconds and the schedule is compressed to make room for it, rather than the
    #: other way round. The compression is capped at 35% of the card so a very short card degrades
    #: to a shorter hold instead of stacking the whole reveal into its first second — and on a long
    #: card the cap never binds and the original rhythm is untouched.
    #: Compressing also improved VO sync here rather than costing it: line two had been landing at
    #: 3.33s against a spoken first sentence that ends at about 2.4s.
    if disclosure and duration_s > 0:
        DISC_HOLD_S = 2.5
        tail_frac = min(0.35, DISC_HOLD_S / duration_s)
        if cta_text:
            #: A CTA is a fifth element on a card built for four, so the schedule is rewritten
            #: rather than stretched: the disclosure keeps its measured hold (the tail is
            #: reserved first, exactly as below), and the four elements before it are spread
            #: across whatever is left. Landing the CTA at ~0.5 of the reserved span puts it up
            #: while the VO says "to learn more", and leaves it on screen through "visit nexus
            #: dot com" — a CTA that arrives ON the words has no time to be read or acted on.
            k = 1.0 - tail_frac
            ONE_START, ONE_END = 0.04 * k, 0.17 * k
            TWO_START, TWO_END = 0.29 * k, 0.43 * k
            RULE_START, RULE_END = 0.47 * k, 0.59 * k
            CTA_START, CTA_END = 0.65 * k, 0.81 * k
            DISC_START, DISC_END = 0.84 * k, k
        else:
            k = (1.0 - tail_frac) / DISC_END
            ONE_START, ONE_END = ONE_START * k, ONE_END * k
            TWO_START, TWO_END = TWO_START * k, TWO_END * k
            RULE_START, RULE_END = RULE_START * k, RULE_END * k
            DISC_START, DISC_END = DISC_START * k, DISC_END * k
    #: The disclosure line must END above this fraction of frame height, because a burned
    #: caption lands under it. Measured 2026-08-29 on a Reap `system_pro_box` pass over this
    #: exact card: the caption box occupied y=0.72..0.79 and cut through the middle of "Made
    #: with AI. Reviewed and posted by a human." A partially occluded Art. 50 line is not a
    #: cosmetic problem, and no gate caught it — `video_lint`'s caption check confirms the
    #: caption is inside the safe area, which it was; it has no idea the disclosure lives there
    #: too. 0.70 clears Reap's observed band by 2% and our own `captions.py` lower block (which
    #: sits lower still, at the bottom of the safe box) by much more.

    ground = _backdrop(w, h, palette)

    #: Prepared ONCE, before the frame loop — resize is not free 250 times over. Placed top-left,
    #: matching the text block's own left margin (`area.left + 0.04`, same inset `max_w` above is
    #: built from) so the mark and the copy share one vertical guide instead of two competing
    #: margins.
    LOGO_TOP_MARGIN_FRAC = 0.035
    LOGO_GAP_FRAC = 0.02  # clearance required between the logo's bottom edge and the spine block
    logo_img: Image.Image | None = None
    logo_x = logo_y = 0
    logo_bottom = 0
    if logo:
        logo_img = _prepare_logo(
            kit, palette=palette, frame_w=w, variant=logo_variant, repo_root=repo_root
        )
        logo_x = round(w * area.left) + round(w * 0.04)
        logo_y = round(h * LOGO_TOP_MARGIN_FRAC)
        logo_bottom = logo_y + logo_img.height

    #: With a CTA the card carries five stacked elements instead of four, and the two that must
    #: not move are at the BOTTOM: the Art. 50 disclosure has to end above the burned caption
    #: band, and the CTA has to sit clear of it. So this variant stacks UPWARD from the band —
    #: disclosure, CTA, rule, then the spine block — instead of downward from a centred block.
    #: Laying it out downward is what put the disclosure into the caption band on the 2026-08-30
    #: cut: each element took its slot from the one above, and the last one absorbed every
    #: rounding error at exactly the position where being unreadable is a compliance problem.
    cta_font = None
    cta_y = disc_y_fixed = 0
    stack_top: int | None = None
    if disclosure and not cta_text:
        # Stack UPWARD from the caption band, exactly as the CTA variant below does and for the
        # same reason. Laying this card out downward from a centred block was fine while the
        # disclosure was one line: the 0.105h offset cleared the rule, and the band clamp rarely
        # fired. Once the line WRAPS (portrait), the clamp lifts the block by more than that gap
        # and the disclosure lands on top of the rule. Anchoring the bottom element to the band
        # and building up from it makes the gaps hold at any line count.
        probe = ImageDraw.Draw(ground)
        band = round(h * CAPTION_BAND_TOP_FRAC)
        block_h_probe = (line_px * 2 + round(h * 0.030)) if spec.line_two else line_px
        disc_y_fixed = band - disc_block_h
        stack_top = disc_y_fixed - round(h * 0.050) - round(h * 0.055) - block_h_probe
        floor = round(h * 0.06)
        if logo_img is not None:
            floor = max(floor, logo_bottom + round(h * LOGO_GAP_FRAC))
        if stack_top < floor:
            raise SceneError(
                f"title card: the disclosure wraps to {len(disc_lines)} lines and pushes the "
                f"spine block to {stack_top / h:.3f}h, off the top of the frame"
            )
    if cta_text and disclosure:
        probe = ImageDraw.Draw(ground)
        block_h_probe = (line_px * 2 + round(h * 0.030)) if spec.line_two else line_px
        cta_font = _fit_or_refuse(
            probe,
            face,
            cta_text,
            max_w,
            round(h * 0.052),
            round(h * 0.036),
            where="title card: CTA",
        )
        band = round(h * CAPTION_BAND_TOP_FRAC)
        cb = probe.textbbox((0, 0), cta_text, font=cta_font)
        disc_y_fixed = band - disc_block_h
        cta_y = disc_y_fixed - round(h * 0.034) - cb[3]
        stack_top = cta_y + cb[1] - round(h * 0.044) - round(h * 0.055) - block_h_probe
        floor = round(h * 0.06)
        if logo_img is not None:
            # The logo occupies a fixed band at the top regardless of how tall the stacked block
            # below it grows; a stack that reaches this high must refuse rather than overlap it,
            # the same "raise, don't render on top of it" rule the plain 0.06h floor already
            # enforces for the frame edge.
            floor = max(floor, logo_bottom + round(h * LOGO_GAP_FRAC))
        if stack_top < floor:
            reason = "the logo, the CTA," if logo_img is not None else "the CTA"
            raise SceneError(
                f"title card: {reason} and the disclosure push the spine block to "
                f"{stack_top / h:.3f}h, off the top of the frame"
            )

    frames: list[Image.Image] = []
    for i in range(n_frames):
        t = i / (n_frames - 1) if n_frames > 1 else 1.0
        img = ground.copy()
        draw = ImageDraw.Draw(img)

        f1 = _fit_or_refuse(
            draw, hero_face, spec.line_one, max_w, line_px, floor_px, where="title card: line one"
        )
        f2 = (
            _fit_or_refuse(
                draw,
                hero_face,
                spec.line_two,
                max_w,
                line_px,
                floor_px,
                where="title card: line two",
            )
            if spec.line_two
            else None
        )
        gap = round(h * 0.030)
        block_h = (line_px * 2 + gap) if spec.line_two else line_px
        top = (
            stack_top
            if stack_top is not None
            else (h - block_h) // 2 - (round(h * 0.045) if disclosure else 0)
        )

        a1 = _ease_in_out(_window(t, ONE_START, ONE_END))
        if a1 > 0:
            x = (w - _text_w(draw, spec.line_one, f1)) / 2
            # Line one is the SETUP and line two the payoff, so one is dimmed and the other is
            # not. On a name-only card there is no payoff line to defer to — the one line IS the
            # statement, and dimming it renders the whole card in secondary type.
            one_rgb = text_rgb if not spec.line_two else dim_rgb
            draw.text((x, top), spec.line_one, font=f1, fill=_lerp_color(canvas_rgb, one_rgb, a1))

        a2 = _ease_in_out(_window(t, TWO_START, TWO_END))
        if f2 is not None and a2 > 0:
            x = (w - _text_w(draw, spec.line_two, f2)) / 2
            y = top + line_px + gap
            draw.text((x, y), spec.line_two, font=f2, fill=_lerp_color(canvas_rgb, text_rgb, a2))

        rule = _ease_in_out(_window(t, RULE_START, RULE_END))
        if rule > 0:
            rw = round(w * 0.10 * rule)
            ry = top + block_h + round(h * 0.055)
            draw.line(
                (w // 2 - rw, ry, w // 2 + rw, ry), fill=accent_rgb, width=stroke_px("heavy", h)
            )

        if cta_font is not None:
            ac = _ease_in_out(_window(t, CTA_START, CTA_END))
            if ac > 0:
                x = (w - _text_w(draw, cta_text, cta_font)) / 2
                draw.text(
                    (x, cta_y),
                    cta_text,
                    font=cta_font,
                    fill=_lerp_color(canvas_rgb, accent_rgb, ac),
                )

        if disc_lines:
            ad = _ease_in_out(_window(t, DISC_START, DISC_END))
            if ad > 0:
                y = disc_y_fixed if stack_top is not None else top + block_h + round(h * 0.105)
                # Clamped against the caption band on the RENDERED box, never on the nominal font
                # size. The first version of this clamp used disc_px and silently did nothing: at
                # 1080 the draw origin was y=712 while the ink ran 755..794, so a 32px "height"
                # understated an 82px reach. textbbox is the only measurement that knows where the
                # glyphs actually land — and with the line wrapped it is the LAST line's box that
                # decides, so the whole block moves together.
                last = draw.textbbox(
                    (0, y + disc_line_h * (len(disc_lines) - 1)), disc_lines[-1], font=disc_font
                )
                overshoot = last[3] - round(h * CAPTION_BAND_TOP_FRAC)
                if overshoot > 0:
                    y -= overshoot
                fill = _lerp_color(canvas_rgb, dim_rgb, ad)
                for n, line in enumerate(disc_lines):
                    x = (w - _text_w(draw, line, disc_font)) / 2
                    draw.text((x, y + disc_line_h * n), line, font=disc_font, fill=fill)

        if logo_img is not None:
            # Arrives with line one rather than waiting its own turn — a persistent brand mark
            # reads as "always there", not as a fifth element in the reveal sequence.
            alogo = _ease_in_out(_window(t, 0.0, 0.10))
            img = img.convert("RGBA")
            _paste_logo(img, logo_img, x=logo_x, y=logo_y, opacity=alogo)

        frames.append(img)

    return _write_frames(frames, out_dir, prefix=spec.prefix)
