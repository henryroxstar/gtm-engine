"""A diagram fitted to a pixel box of a fixed frame, with its type set in frame pixels.

``render_svg`` draws a document figure: the canvas is padded out to at least 800x400 units and the
type is set in units (11.5 for a label, 7 to 8.5 for the rest), so how large anything reads depends
on how big the figure is drawn. That is right on a page and wrong in a frame, where a three-box
diagram came out filling about a quarter of a 1080 px height with labels near 30 px and an
uppercase kind chip on every box. This module is the other framing, for animation-ready markup
that is drawn over a whole frame of known size:

* the viewBox covers the ``viewport`` the page draws the SVG over (a rect of the frame, in frame
  pixels), scaled so the content's bounding box fills ``fill`` of the ``box`` it is given (along
  whichever side binds first) and sits centred in it;
* every string is sized in frame pixels: node labels at ``label_px``, stepping down to ``min_px``
  if a label would not fit its box, and every other string (sublabel, edge label, group label)
  at ``min_px``. A label that does not fit even at ``min_px`` is refused (``FrameFitError``):
  smaller type is the one fix that does not work, because nobody can read it;
* lines are sized in frame pixels too, so a large diagram does not draw hairlines and a small one
  does not draw bars;
* no kind chips.

The element contract is ``animation_ready``'s, unchanged: the same ids are validated the same way,
each node, edge and group is one ``<g>`` carrying its ``data-*`` key, and every colour is a brand
CSS variable. ``render_svg`` is not touched, so its output is byte-identical with or without this
module. The caller decides the box, the fill and the sizes; nothing here knows about safe areas,
captions or brands.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import animation_ready as _ar
from .animation_ready import paint, role
from .ir import DiagramIR, DiagramNode
from .render import (
    _compute_edge_endpoints,
    _compute_orthogonal_elbow,
    _escape,
    _layout_nodes_4px_grid,
    resolve_diagram_theme,
)

#: Width of one character as a fraction of the font size. Deliberately wide: it is the average
#: of the widest fallback face the renderer may land on (a DejaVu-class sans in bold, about 0.62),
#: so the fit check refuses a label that is close rather than letting it spill out of its box.
CHAR_EM = 0.62
#: Line widths in frame pixels: box outlines and connectors, group outlines, label-box outlines.
LINE_PX, GROUP_LINE_PX, HAIRLINE_PX = 3.0, 2.5, 1.5
#: Inner padding of a node, as a fraction of the label size, on each side.
PAD_EM = 0.45


class FrameFitError(ValueError):
    """The diagram cannot be drawn in this box with readable type. The message says what to cut."""


@dataclass(frozen=True)
class FrameFit:
    """Where and how large the diagram is drawn, all in frame pixels."""

    viewport: tuple[float, float, float, float]  # x, y, width, height the SVG is drawn over
    box: tuple[float, float, float, float]  # x, y, width, height the content may occupy
    fill: float  # fraction of the box's binding side the content spans
    label_px: float  # node label size to aim for
    min_px: float  # no string is drawn smaller than this

    def __post_init__(self) -> None:
        if not 0.0 < self.fill <= 1.0:
            raise ValueError(f"fill must be in (0, 1], got {self.fill}")
        if not 0.0 < self.min_px <= self.label_px:
            raise ValueError("min_px must be positive and no larger than label_px")
        for name, rect in (("viewport", self.viewport), ("box", self.box)):
            if rect[2] <= 0 or rect[3] <= 0:
                raise ValueError(f"{name} must have a positive size, got {rect}")


def _text_w(text: str, px: float) -> float:
    return len(text) * CHAR_EM * px


def _group_rect(grp) -> tuple[float, float, float, float]:
    # Same 4px snapping and minimums as render._render_svg_groups.
    gx = float(int(round(grp.x / 4.0)) * 4)
    gy = float(int(round(grp.y / 4.0)) * 4)
    return (
        gx,
        gy,
        float(max(100, int(round(grp.width / 4.0)) * 4)),
        float(max(60, int(round(grp.height / 4.0)) * 4)),
    )


def _content_bounds(
    nodes: list[DiagramNode], groups: list, tab_units: float
) -> tuple[float, float, float, float]:
    xs0, ys0, xs1, ys1 = [], [], [], []
    for n in nodes:
        xs0.append(n.x), ys0.append(n.y), xs1.append(n.x + n.width), ys1.append(n.y + n.height)
    for grp in groups:
        gx, gy, gw, gh = _group_rect(grp)
        top = gy - tab_units / 2.0 if grp.label else gy
        xs0.append(gx), ys0.append(top), xs1.append(gx + gw), ys1.append(gy + gh)
    return min(xs0), min(ys0), max(xs1), max(ys1)


def _scale(fit: FrameFit, nodes: list[DiagramNode], groups: list) -> tuple[float, tuple]:
    """Pixels per diagram unit, and the content bounds it was computed for.

    The group label tab's height is set in pixels, so its height in units depends on the scale;
    two passes settle it (the second changes the bounds by a fraction of one tab).
    """
    _, _, bw, bh = fit.box
    tab_px = fit.min_px * 1.7
    s = 1.0
    bounds = _content_bounds(nodes, groups, 0.0)
    for _ in range(2):
        bounds = _content_bounds(nodes, groups, tab_px / s)
        cw, ch = bounds[2] - bounds[0], bounds[3] - bounds[1]
        s = fit.fill * min(bw / cw, bh / ch)
    return s, bounds


def _label_px(fit: FrameFit, nodes: list[DiagramNode], s: float) -> float:
    """One label size for the whole diagram: the largest from label_px down to min_px that fits."""
    px = fit.label_px
    while px > fit.min_px:
        if all(_text_w(n.label, px) <= n.width * s - 2 * PAD_EM * px for n in nodes):
            break
        px = max(fit.min_px, px - 2.0)
    for n in nodes:
        room = n.width * s - 2 * PAD_EM * px
        for text, size in ((n.label, px), (n.sublabel, fit.min_px)):
            if text and _text_w(text, size) > room:
                raise FrameFitError(
                    f"{text!r} in box {n.id!r} needs about {_text_w(text, size):.0f}px at "
                    f"{size:.0f}px type, and the box is {room:.0f}px wide inside. Shorten it, or "
                    "draw fewer elements so each box can be larger; smaller type would not be read."
                )
        block = px + (0.35 * px + fit.min_px if n.sublabel else 0.0)
        if block > n.height * s - PAD_EM * px:
            raise FrameFitError(
                f"box {n.id!r} is {n.height * s:.0f}px tall and its text needs {block:.0f}px. "
                "Draw fewer elements so each box can be larger, or drop its second line."
            )
    return px


def _groups_svg(groups: list, theme: dict[str, str], u, min_px: float) -> list[str]:
    out: list[str] = []
    stroke, fill, surface = _ar.group_paints()
    for grp in groups:
        gx, gy, gw, gh = _group_rect(grp)
        parts = [
            f'  <rect x="{gx:.1f}" y="{gy:.1f}" width="{gw:.1f}" height="{gh:.1f}" rx="{u(16):.2f}" '
            f'{paint(("fill", fill), ("stroke", stroke))} stroke-width="{u(GROUP_LINE_PX):.3f}" '
            f'stroke-dasharray="{u(14):.2f},{u(10):.2f}"/>'
        ]
        if grp.label:
            label = grp.label.strip()
            tab_h, tab_w = u(min_px * 1.7), u(_text_w(label, min_px) + min_px * 1.4)
            tx, ty = gx + u(min_px), gy - tab_h / 2.0
            parts.append(
                f'  <rect x="{tx:.1f}" y="{ty:.1f}" width="{tab_w:.1f}" height="{tab_h:.1f}" '
                f'rx="{u(6):.2f}" {paint(("fill", surface), ("stroke", stroke))} '
                f'stroke-width="{u(HAIRLINE_PX):.3f}"/>'
            )
            parts.append(
                f'  <text x="{tx + tab_w / 2.0:.1f}" y="{gy + u(min_px * 0.35):.1f}" '
                f'{paint(("fill", stroke))} font-size="{u(min_px):.2f}" font-weight="600" '
                f'font-family="{theme["font_mono"]}" text-anchor="middle">{_escape(label)}</text>'
            )
        out.extend(_ar.wrap(parts, "data-group-id", _escape(grp.id), True))
    return out


def _edges_svg(edges: list, node_map: dict, theme: dict[str, str], u, min_px: float) -> list[str]:
    out: list[str] = []
    for edge in edges:
        x1, y1, x2, y2 = _compute_edge_endpoints(node_map[edge.source], node_map[edge.target], edge)
        stroke, lbl_fill, lbl_stroke, lbl_text = _ar.edge_paints(edge.kind)
        marker = {"accent": "url(#arrow-accent)", "primary": "url(#arrow-primary)"}.get(
            edge.kind, "url(#arrow)"
        )
        parts = [
            f'  <path d="{_compute_orthogonal_elbow(x1, y1, x2, y2, r=u(12))}" '
            f'{paint(("fill", "none"), ("stroke", stroke))} stroke-width="{u(LINE_PX):.3f}" '
            f'marker-end="{marker}"/>'
        ]
        if edge.label:
            text = edge.label.replace("\r", "").replace("\n", " ")
            w, h = u(_text_w(text, min_px) + min_px), u(min_px * 1.6)
            mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            parts.append(
                f'  <rect x="{mx - w / 2.0:.1f}" y="{my - h / 2.0:.1f}" width="{w:.1f}" '
                f'height="{h:.1f}" rx="{u(6):.2f}" '
                f"{paint(('fill', lbl_fill), ('stroke', lbl_stroke))} "
                f'stroke-width="{u(HAIRLINE_PX):.3f}"/>'
            )
            parts.append(
                f'  <text x="{mx:.1f}" y="{my + u(min_px * 0.35):.1f}" {paint(("fill", lbl_text))} '
                f'font-size="{u(min_px):.2f}" font-weight="500" font-family="{theme["font_mono"]}" '
                f'text-anchor="middle">{_escape(text)}</text>'
            )
        out.extend(_ar.wrap(parts, "data-edge", _escape(f"{edge.source}->{edge.target}"), True))
    return out


def _nodes_svg(nodes: list, theme: dict[str, str], u, label_px: float, min_px: float) -> list[str]:
    out: list[str] = []
    for node in nodes:
        fill, stroke, _chip_bg, _chip_stroke, _chip_text, text = _ar.node_paints(node)
        cx, cy = node.x + node.width / 2.0, node.y + node.height / 2.0
        gap = 0.35 * label_px
        block = label_px + (gap + min_px if node.sublabel else 0.0)
        top = cy - u(block) / 2.0
        parts = [
            f'  <rect x="{node.x:.1f}" y="{node.y:.1f}" width="{node.width:.1f}" '
            f'height="{node.height:.1f}" rx="{u(12):.2f}" '
            f'{paint(("fill", fill), ("stroke", stroke))} stroke-width="{u(LINE_PX):.3f}"/>',
            f'  <text x="{cx:.1f}" y="{top + u(label_px * 0.8):.1f}" {paint(("fill", text))} '
            f'font-size="{u(label_px):.2f}" font-weight="600" font-family="{theme["font_sans"]}" '
            f'text-anchor="middle">{_escape(node.label)}</text>',
        ]
        if node.sublabel:
            parts.append(
                f'  <text x="{cx:.1f}" y="{top + u(label_px + gap + min_px * 0.8):.1f}" '
                f'{paint(("fill", role("muted")))} font-size="{u(min_px):.2f}" '
                f'font-family="{theme["font_mono"]}" text-anchor="middle">'
                f"{_escape(node.sublabel)}</text>"
            )
        out.extend(_ar.wrap(parts, "data-node-id", _escape(node.id), True))
    return out


def render_frame_svg(ir: DiagramIR, kit: dict | None, fit: FrameFit) -> str:
    """Animation-ready SVG of ``ir`` whose viewBox is ``fit.viewport`` (see module doc)."""
    ir = _ar.prepare(ir)  # validates ids and works on a copy, exactly as animation_ready does
    theme = {k: _escape(v) for k, v in resolve_diagram_theme(kit).items()}
    nodes, edges, groups = list(ir.nodes), list(ir.edges), list(ir.groups)
    _layout_nodes_4px_grid(nodes, edges, ir.direction)

    s, (x0, y0, x1, y1) = _scale(fit, nodes, groups)
    label_px = _label_px(fit, nodes, s)

    def u(px: float) -> float:
        return px / s

    bx, by, bw, bh = fit.box
    px, py, pw, ph = fit.viewport
    vw, vh = pw / s, ph / s
    vx = (x0 + x1) / 2.0 - (bx + bw / 2.0 - px) / s
    vy = (y0 + y1) / 2.0 - (by + bh / 2.0 - py) / s
    slug = "".join(c if c.isalnum() or c in "_-" else "-" for c in (ir.slug or "diagram"))
    slug = slug.strip("-") or "diagram"
    m_muted, m_accent, m_primary, m_link, bg = _ar.header_fills()
    markers = "".join(
        f'    <marker id="{mid}" markerWidth="8" markerHeight="6" refX="7" refY="3" '
        f'orient="auto">\n      <polygon points="0 0, 8 3, 0 6" {fill_attr}/>\n    </marker>\n'
        for mid, fill_attr in (
            ("arrow", m_muted),
            ("arrow-accent", m_accent),
            ("arrow-primary", m_primary),
            ("arrow-link", m_link),
        )
    )
    svg = [
        f'<svg data-gtm-diagram="1" viewBox="{vx:.2f} {vy:.2f} {vw:.2f} {vh:.2f}" '
        f'role="img" aria-labelledby="{slug}-title {slug}-desc">',
        f'  <title id="{slug}-title">{_escape(ir.title.replace("[", "").replace("]", ""))}</title>',
        f'  <desc id="{slug}-desc">{_escape(ir.description)}</desc>',
        "  <defs>\n" + markers + "  </defs>",
        f'  <rect x="{vx:.2f}" y="{vy:.2f}" width="{vw:.2f}" height="{vh:.2f}" {bg}/>',
    ]
    node_map = {n.id: n for n in nodes}
    svg.extend(_groups_svg(groups, theme, u, fit.min_px))
    svg.extend(_edges_svg(edges, node_map, theme, u, fit.min_px))
    svg.extend(_nodes_svg(nodes, theme, u, label_px, fit.min_px))
    svg.append("</svg>")
    return "\n".join(svg)
