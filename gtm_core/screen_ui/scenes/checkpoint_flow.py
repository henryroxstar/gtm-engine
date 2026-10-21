from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from PIL import Image, ImageDraw

from ...captions import FontMissing, load_face, load_faces
from ...design_tokens import resolve_motion, stroke_px
from ..base import SceneError
from ..draw import (
    CAPTION_BAND_TOP_FRAC,
    _arrive,
    _arrow_head,
    _backdrop,
    _ease_in_out,
    _ease_named,
    _entrance,
    _font,
    _join_dot,
    _lerp,
    _lerp_color,
    _panel,
    _text_tracked,
    _text_w,
    _tracked_w,
    _window,
)
from ..fit import _PAYLOAD_MIN_H_FRAC, _SECONDARY_MIN_H_FRAC, _fit_or_refuse, _fit_tracked
from ..frames import _frame_count, _write_frames
from ..palette import _hex_to_rgb, _load_palette, _resolve_area
from .checkpoint_timing import _CHECKPOINT_DEFAULT_TIMING, _resolve_timing

#: On-screen names for the two product layers this card draws. Generic on purpose: a tenant
#: passes its own product names as ``labels`` (``--label gateway=... --label stream=...`` on
#: the CLI) and engine code never names a tenant's product. The previous literals were one
#: tenant's real product names written in capitals, which is how they shipped past the
#: export's case-sensitive marker sweep until 2026-09-03.
_DEFAULT_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "gateway": "THE CHECKPOINT",
        "gateway_sub": "in front of your systems",
        "stream": "THE GUARDRAILS",
    }
)


def render_checkpoint_flow_frames(
    *,
    kit: dict,
    ratio: str,
    fps: int,
    duration_s: float,
    out_dir: Path,
    font_role: str = "caption",
    repo_root: Path | None = None,
    timing: Mapping[str, tuple[float, float]] | None = None,
    labels: Mapping[str, str] | None = None,
) -> int:
    """Caller, agent, records — then the gateway drops in and the reach reroutes through it.

    The REROUTE is the idea, which is why this is a scene and not one of the existing static flow
    infographics: a still diagram of the end state shows a checkpoint, but not that everything now
    has to pass it.

    This card is also where the product is NAMED. Until 2026-08-30 the film showed the solution —
    this diagram, and a console screenshot at 1:40 — without ever saying what it was, so the close
    argued for a shape rather than for a thing the viewer could go and get. The checkpoint node
    carries the product name and the line beneath it carries the job, in that order.

    ``labels`` is where that name comes from: ``gateway`` / ``gateway_sub`` / ``stream``
    override :data:`_DEFAULT_LABELS` with the tenant's own product names. An unknown key is a
    SceneError rather than a silently ignored typo.
    """
    area = _resolve_area(ratio)
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

    canvas_rgb = _hex_to_rgb(palette.canvas, field="canvas")
    text_rgb = _hex_to_rgb(palette.text, field="text")
    dim_rgb = _hex_to_rgb(palette.text_dim, field="text_dim")
    accent_rgb = _hex_to_rgb(palette.accent, field="accent")

    w, h = area.width, area.height
    x0 = round(w * area.left) + round(w * 0.03)
    x1 = w - round(w * area.right) - round(w * 0.03)
    inner = x1 - x0

    node_font = _font(face, round(h * _PAYLOAD_MIN_H_FRAC))
    small_font = _font(faces.body, round(h * 0.036))
    cp_sub_font = _font(faces.body, round(h * _SECONDARY_MIN_H_FRAC))

    node_w = round(inner * 0.30)
    node_h = round(h * 0.125)
    # 0.100h, not 0.145h. Measured 2026-09-03 against the TEXT BAND (0.080h..0.700h — the safe
    # area cut off where a caption is burned in, which is the denominator that actually binds;
    # measuring against the safe box instead reports every card as ~20% empty and is wrong):
    # every other card in this film fills 91.6%..99.3% of that band and this one filled 79.9%.
    # It was the family's one outlier, so a cut into it read as the frame shrinking. The three
    # tiers now start higher and breathe wider — see the two gaps below.
    row_y = round(h * 0.100)
    # "THE AGENT", never "the helper". The film's script banned the word `agent` for a grade-5
    # reading target aimed at an audience who is not the buyer; the script was corrected on
    # 2026-08-30 but this label is drawn in code, so the rewrite could not reach it and it shipped
    # saying HELPER under a VO saying AGENT. `tests/lint/vocab_check.py` now reads the drawn
    # strings, which is the only layer that would have caught it.
    nodes = [
        ("SOMEONE CALLING", x0),
        ("THE AGENT", x0 + (inner - node_w) // 2),
        ("YOUR RECORDS", x1 - node_w),
    ]

    # 0.162h, not 0.155h: the Stream panel now carries two sub-lines instead of one, and at
    # 0.155h the second sat 3px from the panel floor and read as hanging out of the box. The
    # gap to the note box below absorbs the growth (0.055h -> 0.050h) so the note's bottom
    # edge stays clear of the burned caption band, which is the constraint that actually binds.
    cp_w, cp_h = round(inner * 0.34), round(h * 0.162)
    cp_x = x0 + (inner - cp_w) // 2 + round(inner * 0.14)
    cp_y = row_y + node_h + round(h * 0.135)
    # The stream layer hangs off the agent on the LEFT, mirroring the gateway on the right, since
    # that is literally where the two products sit: STREAM governs the agent's own model traffic
    # (guardrails on what it will say and do), GATEWAY governs what the agent may reach in your
    # systems (identity, scope, and the record). Naming the wrong one for the wrong job is the
    # classic error with this pair, and the symmetry is the argument h14 makes out loud — two
    # layers, two jobs, and you want both.
    st_w, st_h = round(inner * 0.30), cp_h
    st_x = x0
    st_y = cp_y
    note_y = cp_y + cp_h + round(h * 0.090)
    # RECOMPOSED 2026-09-03. This was a 0.42-inner box hard against the left margin, two lines
    # tall, with the whole bottom-right of the frame empty behind it. Measured on the delivered
    # card, lit-pixel density by quadrant ran top-left 4.47%, top-right 4.70%, bottom-left 3.70%
    # and bottom-right 0.81% — the payoff frame of the film was five times emptier on one side
    # than the other, and the bottom third used 32% of the width against 82% in the top third.
    #
    # It is now a STRIP spanning the full middle tier, from Stream's left edge to the gateway's
    # right edge, one line tall: the label left, its qualifier right. That is a better argument as
    # well as a better balance — the record runs UNDER both layers, which is what "written every
    # time, including the noes" claims, where a box parked beside them read as a third sibling.
    # It also lets the gateway's connector drop STRAIGHT into it instead of elbowing left.
    note_x = st_x
    note_w = (cp_x + cp_w) - st_x
    note_h_px = round(h * 0.078)
    node_pad = round(h * 0.024)

    # The note box is the LOWEST thing this card draws, and a burned caption is laid over the
    # finished film at `CAPTION_BAND_TOP_FRAC`. The shipped cut put this box at 0.775h — inside
    # that band — so the caption and the card's own words stacked on top of each other and neither
    # could be read. `title-close` was given this clamp when the disclosure line hit the same band;
    # the flow card never was, because each scene was carrying its own idea of where the bottom is.
    # Refusing is right: the fix is a layout change, and a scene that silently drew into the
    # caption is exactly the failure that shipped.
    if note_y + note_h_px > round(h * CAPTION_BAND_TOP_FRAC):
        raise SceneError(
            f"checkpoint-flow: the note box ends at "
            f"{(note_y + note_h_px) / h:.3f}h, inside the caption band that starts at "
            f"{CAPTION_BAND_TOP_FRAC}h — a burned caption would land on it"
        )

    motion = resolve_motion(kit)
    n_frames = _frame_count(fps, duration_s)
    # Cued off the MEASURED narration, never spread evenly. This card is the film's payoff and its
    # VO names each element in turn, so an even schedule desynchronises every one of them: on the
    # 2026-08-30 cut the gateway dropped in at 0.38 under a voice that had named it at 0.11, and
    # the card spent its first third saying nothing.
    #
    # The windows used to be sixteen literals here, hand-transcribed from one narration's word
    # timestamps by a person reading them off a screen, under a comment asking whoever re-cut that
    # VO to remember to re-derive them. `timing` is that comment turned into a mechanism: pass the
    # map `gtm_core.vo_timings` builds from `<id>.words.json` and the card follows the voice that
    # actually exists. Absent it, `_CHECKPOINT_DEFAULT_TIMING` still applies — and the scene's JSON
    # says which was used, so "measured" and "assumed" are distinguishable after the fact.
    t_map = _resolve_timing("checkpoint-flow", _CHECKPOINT_DEFAULT_TIMING, timing)
    unknown = sorted(set(labels or {}) - set(_DEFAULT_LABELS))
    if unknown:
        raise SceneError(
            f"checkpoint-flow: unknown label key(s) {unknown} — expected {sorted(_DEFAULT_LABELS)}"
        )
    lbl_map = {**_DEFAULT_LABELS, **(labels or {})}
    DROP_START, DROP_END = t_map["DROP"]
    REROUTE_START, REROUTE_END = t_map["REROUTE"]
    NOTE_START, NOTE_END = t_map["NOTE"]
    STREAM_START, STREAM_END = t_map["STREAM"]
    SUB1_START, SUB1_END = t_map["SUB1"]
    SUB2_START, SUB2_END = t_map["SUB2"]
    # Not narration-cued: the diagram establishing itself before the voice names anything. Derived
    # from the first real cue with a small lead, so a re-timed VO carries these along instead of
    # leaving three literals behind that now describe a different recording.
    # Offsets are BEFORE the first cue, preserving the shipped card's own lead-in exactly
    # (NODES_END 0.10, LINK 0.065-0.100, DIRECT 0.105-0.140 against a DROP at 0.145). They stay
    # fractions of the card rather than seconds, because the establishing beat is a proportion of
    # the whole — a longer VO gets a proportionally longer settle, which is what it should get.
    NODES_END = max(0.0, DROP_START - 0.045)
    LINK_START, LINK_END = max(0.0, DROP_START - 0.080), max(0.0, DROP_START - 0.045)
    DIRECT_START, DIRECT_END = max(0.0, DROP_START - 0.040), max(0.0, DROP_START - 0.005)

    ground = _backdrop(w, h, palette)
    frames: list[Image.Image] = []
    for i in range(n_frames):
        t = i / (n_frames - 1) if n_frames > 1 else 1.0
        img = ground.copy()
        draw = ImageDraw.Draw(img)

        reroute = _ease_in_out(_window(t, REROUTE_START, REROUTE_END))
        direct = _ease_in_out(_window(t, DIRECT_START, DIRECT_END)) * (1.0 - reroute)

        for n, (label, nx) in enumerate(nodes):
            # Entrances ease OUT and RISE the last few pixels into place. A symmetric fade with no
            # displacement is the "boring" note in its purest form: nothing arrives, three
            # rectangles simply become visible.
            a, oy = _arrive(
                _entrance(n, motion=motion, duration_s=duration_s, h=h, start=n * (NODES_END / 3)),
                t,
            )
            if a <= 0:
                continue
            box = (nx, row_y + oy, nx + node_w, row_y + node_h + oy)
            _panel(img, box, palette, alpha=a)
            draw = ImageDraw.Draw(img)
            nf = _fit_tracked(
                draw, face, label, node_w - 2 * node_pad, node_font.size, round(h * 0.034)
            )
            tw = _tracked_w(draw, label, nf)
            _text_tracked(
                draw,
                (nx + (node_w - tw) / 2, row_y + (node_h - nf.size) / 2 - round(h * 0.006) + oy),
                label,
                nf,
                _lerp_color(canvas_rgb, text_rgb, a),
            )

        mid_y = row_y + node_h // 2
        a_end, b_start = x0 + node_w, nodes[1][1]
        b_end, c_start = nodes[1][1] + node_w, x1 - node_w
        link = _ease_in_out(_window(t, LINK_START, LINK_END))
        if link > 0:
            lx_end = a_end + round((b_start - a_end) * link)
            draw.line(
                (a_end, mid_y, lx_end, mid_y),
                fill=_lerp_color(canvas_rgb, dim_rgb, link),
                width=stroke_px("emphasis", h),
            )
            _join_dot(draw, a_end, mid_y, _lerp_color(canvas_rgb, dim_rgb, link), h, link)
        if direct > 0:
            dx_end = b_end + round((c_start - b_end) * direct)
            col = _lerp_color(canvas_rgb, accent_rgb, direct)
            draw.line((b_end, mid_y, dx_end, mid_y), fill=col, width=stroke_px("heavy", h))
            _join_dot(draw, b_end, mid_y, col, h, direct)
            _arrow_head(draw, dx_end, mid_y, col, h, when=direct > 0.85)

        # The gateway's drop-in is the card's one narrative gesture, so it keeps its authored
        # amplitude (0.070h — further than the generic `--tempo-travel`) but runs on the PORTED
        # entrance curve, which is what puts it in the same motion language as everything else.
        drop = _ease_named("entrance", _window(t, DROP_START, DROP_END))
        if drop > 0:
            cy = round(_lerp(cp_y - round(h * 0.070), cp_y, drop))
            # The ONE live element on the card, and the only one that blooms. Everything else is
            # a surface; this is the thing being argued for.
            _panel(img, (cp_x, cy, cp_x + cp_w, cy + cp_h), palette, alpha=drop, active=True)
            draw = ImageDraw.Draw(img)
            lbl, sub = lbl_map["gateway"], lbl_map["gateway_sub"]
            cf = _fit_tracked(
                draw, face, lbl, cp_w - 2 * node_pad, node_font.size, round(h * 0.034)
            )
            sf = _fit_or_refuse(
                draw,
                face,
                sub,
                cp_w - 2 * node_pad,
                cp_sub_font.size,
                round(h * 0.028),
                where="checkpoint-flow gateway sub",
            )
            _text_tracked(
                draw,
                (cp_x + (cp_w - _tracked_w(draw, lbl, cf)) / 2, cy + round(h * 0.030)),
                lbl,
                cf,
                _lerp_color(canvas_rgb, text_rgb, drop),
            )
            draw.text(
                (cp_x + (cp_w - _text_w(draw, sub, sf)) / 2, cy + round(h * 0.096)),
                sub,
                font=sf,
                fill=_lerp_color(canvas_rgb, dim_rgb, drop),
            )

            if reroute > 0:
                # Down from the helper, across, and up into records — the same reach, now bent
                # through the checkpoint. Drawn as three segments so it reads as a detour.
                # Legs stop at the gateway's EDGES — down into its top, out of its right side.
                # Routing them to its centre drew the reach straight across the panel and through
                # its own label, which reads as a line ignoring the checkpoint rather than passing
                # through it: the exact opposite of what this card argues.
                my = cy + cp_h // 2
                drop_x = nodes[1][1] + node_w // 2
                up_x = c_start + node_w // 2
                legs = [
                    ((drop_x, row_y + node_h), (drop_x, cy)),
                    ((cp_x + cp_w, my), (up_x, my)),
                    ((up_x, my), (up_x, row_y + node_h)),
                ]
                per = 1.0 / len(legs)
                for k, ((sx, sy), (ex, ey)) in enumerate(legs):
                    seg = _ease_in_out(_window(reroute, k * per, (k + 1) * per))
                    if seg <= 0:
                        continue
                    ex_now, ey_now = round(_lerp(sx, ex, seg)), round(_lerp(sy, ey, seg))
                    draw.line(
                        (sx, sy, ex_now, ey_now), fill=accent_rgb, width=stroke_px("heavy", h)
                    )
                    _join_dot(draw, sx, sy, accent_rgb, h, seg)
                    # A chevron only on the LAST leg, and only once it lands: the reach now
                    # arrives at your records THROUGH the checkpoint, and the arrow is what says
                    # "arrives" rather than "stops here".
                    _arrow_head(
                        draw,
                        ex_now,
                        ey_now,
                        accent_rgb,
                        h,
                        direction="up",
                        when=k == len(legs) - 1 and seg > 0.85,
                    )

        # Arrives with the reroute, one beat after the gateway, so the pair reads as two layers
        # rather than as one diagram with a spare box in it.
        sa, sy_off = _arrive(
            _entrance(0, motion=motion, duration_s=duration_s, h=h, start=STREAM_START), t
        )
        if sa > 0:
            # Elbowed down-and-left from THE AGENT, not straight up from the Stream box: the box
            # sits under "SOMEONE CALLING", so a plain vertical riser drew a line saying the
            # CALLER is what Stream governs. It governs the agent's own model traffic.
            s_ax = nodes[1][1] + round(node_w * 0.25)
            s_cx = st_x + st_w // 2
            s_elbow = st_y - round(h * 0.045) + sy_off
            s_col = _lerp_color(canvas_rgb, accent_rgb, 0.55 * sa)
            for k, ((sx_, sy_), (ex_, ey_)) in enumerate(
                (
                    ((s_ax, row_y + node_h), (s_ax, s_elbow)),
                    ((s_ax, s_elbow), (s_cx, s_elbow)),
                    ((s_cx, s_elbow), (s_cx, st_y + sy_off)),
                )
            ):
                seg = _ease_in_out(_window(sa, k / 3, (k + 1) / 3))
                if seg <= 0:
                    continue
                draw.line(
                    (sx_, sy_, round(_lerp(sx_, ex_, seg)), round(_lerp(sy_, ey_, seg))),
                    fill=s_col,
                    width=stroke_px("emphasis", h),
                )
            _panel(
                img,
                (st_x, st_y + sy_off, st_x + st_w, st_y + st_h + sy_off),
                palette,
                alpha=sa,
            )
            draw = ImageDraw.Draw(img)
            s_lbl = lbl_map["stream"]
            sf_lbl = _fit_tracked(
                draw, face, s_lbl, st_w - 2 * node_pad, node_font.size, round(h * 0.034)
            )
            _text_tracked(
                draw,
                (
                    st_x + (st_w - _tracked_w(draw, s_lbl, sf_lbl)) / 2,
                    st_y + round(h * 0.030) + sy_off,
                ),
                s_lbl,
                sf_lbl,
                _lerp_color(canvas_rgb, text_rgb, sa),
            )
            # Two sub-lines, arriving on their own words. The single line this replaced
            # ("guardrails on what the model says") said the whole job at once, which is fine on a
            # 16s card and dead air on a 26.6s one — and it also dropped the half the VO now
            # names first: the deterministic filter on the request and response seams.
            for s_sub, s_dy, s_win in (
                ("input and output filters", 0.088, (SUB1_START, SUB1_END)),
                ("controls on what it says", 0.126, (SUB2_START, SUB2_END)),
            ):
                sub_a, _ = _arrive(
                    _entrance(
                        0,
                        motion=motion,
                        duration_s=duration_s,
                        h=h,
                        start=s_win[0],
                        cls="soft_fade",
                    ),
                    t,
                )
                if sub_a <= 0:
                    continue
                # Sized at 0.030h rather than fitted down from the 0.038h secondary size: two
                # lines 0.038h apart on a 0.162h panel overlap, and the first version drew the
                # second line straight through the first's descenders. The dy values below are
                # the line positions this size needs, not the other way round.
                sf_sub = _fit_or_refuse(
                    draw,
                    face,
                    s_sub,
                    st_w - 2 * node_pad,
                    round(h * 0.030),
                    round(h * 0.024),
                    where="checkpoint-flow stream sub",
                )
                draw.text(
                    (
                        st_x + (st_w - _text_w(draw, s_sub, sf_sub)) / 2,
                        st_y + round(h * s_dy) + sy_off,
                    ),
                    s_sub,
                    font=sf_sub,
                    fill=_lerp_color(canvas_rgb, dim_rgb, sub_a),
                )

        na, note_dy = _arrive(
            _entrance(
                0,
                motion=motion,
                duration_s=duration_s,
                h=h,
                start=NOTE_START,
                room=round(h * CAPTION_BAND_TOP_FRAC) - (note_y + note_h_px),
            ),
            t,
        )
        if na > 0:
            ny = note_y + note_dy
            nh = note_h_px
            # Tie the note to the checkpoint that writes it. Floating free, it read as an
            # unrelated caption rather than as an output of the thing above it. Now that the
            # strip runs beneath the gateway the tie is a STRAIGHT DROP — the elbow that used to
            # travel left across the frame existed only because the box was parked in the corner.
            drop_x = cp_x + cp_w // 2
            draw.line(
                (drop_x, cp_y + cp_h, drop_x, round(_lerp(cp_y + cp_h, ny, na))),
                fill=_lerp_color(canvas_rgb, accent_rgb, na),
                width=stroke_px("emphasis", h),
            )
            _join_dot(draw, drop_x, cp_y + cp_h, accent_rgb, h, na)
            _panel(img, (note_x, ny, note_x + note_w, ny + nh), palette, alpha=na)
            draw = ImageDraw.Draw(img)
            t1, t2 = "A NOTE OF WHAT HAPPENED", "written every time, including the noes"
            # One baseline, label left and qualifier right, because the strip is now wide enough
            # to hold both and stacking them would leave the right half of a 0.81%-dense quadrant
            # empty for a second time.
            half = note_w // 2 - node_pad
            f1 = _fit_tracked(draw, face, t1, half, small_font.size, round(h * 0.026))
            f2 = _fit_or_refuse(
                draw,
                face,
                t2,
                half,
                small_font.size,
                round(h * 0.026),
                where="checkpoint-flow note sub",
            )
            _text_tracked(
                draw,
                (note_x + node_pad, ny + (nh - f1.size) / 2 - round(h * 0.004)),
                t1,
                f1,
                _lerp_color(canvas_rgb, text_rgb, na),
            )
            draw.text(
                (
                    note_x + note_w - node_pad - _text_w(draw, t2, f2),
                    ny + (nh - f2.size) / 2 - round(h * 0.004),
                ),
                t2,
                font=f2,
                fill=_lerp_color(canvas_rgb, dim_rgb, na),
            )

        frames.append(img)

    return _write_frames(frames, out_dir, prefix="checkpoint-flow")
