"""Native editorial SVG and HTML diagram renderer.

Implements the diagram design system:
- Strict XML and HTML escaping for untrusted labels (injection defense)
- Dynamic brand token resolution from BRAND.toml / brandkit
- 4px grid enforcement and r=8 orthogonal connectors
- Accessible SVG contract (role="img", aria-labelledby, prefixed IDs)
"""

from __future__ import annotations

import math
import re

# saxutils.escape() only escapes text for output; it never parses XML, so there is no
# XXE/entity-expansion surface for defusedxml to close.
import xml.sax.saxutils as saxutils  # nosec B406  # nosemgrep: use-defused-xml
from pathlib import Path

from . import animation_ready as _ar
from .animation_ready import DiagramValidationError, Paint, paint, role
from .ir import DiagramEdge, DiagramGroup, DiagramIR, DiagramNode

__all__ = ["DiagramValidationError", "render_html", "render_svg", "resolve_diagram_theme"]


def _escape(text: str) -> str:
    """Safely escape text for XML / SVG attributes and text nodes, stripping invalid XML 1.0 control chars."""
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(text))
    return saxutils.escape(cleaned, {'"': "&quot;", "'": "&apos;"})


_DEFAULT_THEME: dict[str, str] = {
    "paper": "#f5f5f5",
    "surface": "#ffffff",
    "ink": "#2d3142",
    "primary": "#2d3142",
    "accent": "#eb6c36",
    "muted": "#4f5d75",
    "soft": "#7a8399",
    "rule": "#e2e5ea",
    "link": "#2e5aa8",
    "font_display": "'Tenon', 'Inter', -apple-system, sans-serif",
    "font_sans": "'Tenon', 'Inter', -apple-system, sans-serif",
    "font_mono": "'SF Mono', 'JetBrains Mono', ui-monospace, monospace",
}


def _is_dark_hex(sec: str) -> bool:
    if sec.startswith("#") and len(sec) == 7:
        try:
            r = int(sec[1:3], 16)
            g = int(sec[3:5], 16)
            b = int(sec[5:7], 16)
            lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
            return lum < 0.65
        except ValueError:
            return False
    return True


def _load_diagram_kit_dict(
    profiles_root: Path | dict[str, str | object] | None,
    profile: str | None,
    product: str | None,
) -> dict[str, object] | None:
    if isinstance(profiles_root, dict):
        return profiles_root
    if not profile:
        return None
    root = profiles_root if profiles_root is not None else Path("profiles")
    try:
        from gtm_core.brandkit import load_brand_kit

        return load_brand_kit(root, profile, product)
    except Exception:  # nosec B110
        return None


def _apply_diagram_palette(
    theme: dict[str, str], palette: dict[str, object], *, is_dark_first: bool
) -> None:
    key_map: list[tuple[str, tuple[str, ...]]] = (
        [
            ("paper", ("canvas", "background", "canvas_light", "background_light")),
            ("surface", ("surface", "surface_light")),
            ("ink", ("ink", "text", "ink_light", "text_dark")),
            ("primary", ("primary",)),
            ("accent", ("accent", "primary")),
            ("rule", ("rule", "rule_light")),
        ]
        if is_dark_first
        else [
            ("paper", ("canvas_light", "canvas", "background_light", "background")),
            ("surface", ("surface_light", "surface")),
            ("ink", ("ink_light", "ink", "text_dark", "text")),
            ("primary", ("primary",)),
            ("accent", ("accent", "primary")),
            ("rule", ("rule_light", "rule")),
        ]
    )
    for target, sources in key_map:
        for src in sources:
            if palette.get(src):
                theme[target] = str(palette[src])
                break
    if is_dark_first:
        for m_src in ("ink_muted", "muted", "secondary"):
            if palette.get(m_src):
                theme["muted"] = str(palette[m_src])
                break
    elif "secondary" in palette and _is_dark_hex(str(palette["secondary"])):
        theme["muted"] = str(palette["secondary"])


def resolve_diagram_theme(
    profiles_root: Path | dict[str, str | object] | None = None,
    profile: str | None = None,
    product: str | None = None,
) -> dict[str, str]:
    """Resolve brand tokens from the active profile's brandkit with safe defaults."""
    theme = dict(_DEFAULT_THEME)
    kit = _load_diagram_kit_dict(profiles_root, profile, product)
    if not isinstance(kit, dict):
        return theme

    try:
        ds = kit.get("design_system", {})
        is_dark_first = isinstance(ds, dict) and str(ds.get("archetype") or "") == "mission-control"
        palette = kit.get("palette", {})
        if isinstance(palette, dict):
            _apply_diagram_palette(theme, palette, is_dark_first=is_dark_first)

        typo = kit.get("typography", {})
        if isinstance(typo, dict):
            raw_display = str(typo.get("display") or typo.get("primary") or typo.get("body") or "")
            clean_display = re.sub(r"[^\w\s-]", "", raw_display).strip()
            if clean_display:
                theme["font_display"] = f"'{clean_display}', -apple-system, sans-serif"
                theme["font_sans"] = f"'{clean_display}', -apple-system, sans-serif"
            raw_mono = str(typo.get("mono") or "")
            clean_mono = re.sub(r"[^\w\s-]", "", raw_mono).strip()
            if clean_mono:
                theme["font_mono"] = f"'{clean_mono}', ui-monospace, monospace"
    except Exception:  # nosec B110
        pass

    return theme


def _layout_nodes_4px_grid(
    nodes: list[DiagramNode], edges: list[DiagramEdge], direction: str
) -> None:
    """Ensure all nodes have positions and dimensions aligned to the 4px grid."""
    has_pos = any(n.x != 0.0 or n.y != 0.0 for n in nodes)
    if not has_pos:
        # Topological / sequential layer layout
        is_horizontal = direction.upper() in ("LR", "RL")
        start_x = 40.0
        start_y = 60.0
        gap_x = 64.0
        gap_y = 48.0
        w = 140.0
        h = 80.0

        for i, node in enumerate(nodes):
            node.width = w
            node.height = h
            if is_horizontal:
                node.x = start_x + i * (w + gap_x)
                node.y = start_y + ((i % 2) * 80.0)
            else:
                node.x = start_x + ((i % 2) * 100.0)
                node.y = start_y + i * (h + gap_y)

    for n in nodes:
        n.x = float(int(round(n.x / 4.0)) * 4)
        n.y = float(int(round(n.y / 4.0)) * 4)
        n.width = float(max(80, int(round(n.width / 4.0)) * 4))
        n.height = float(max(48, int(round(n.height / 4.0)) * 4))


def _compute_orthogonal_elbow(x1: float, y1: float, x2: float, y2: float, r: float = 8.0) -> str:
    """Compute an orthogonal right-angle elbow path with quarter-arc corners (radius r)."""
    if abs(x1 - x2) < 1.0 or abs(y1 - y2) < 1.0:
        return f"M {x1:.1f} {y1:.1f} L {x2:.1f} {y2:.1f}"

    mid_x = float(int(round(((x1 + x2) / 2.0) / 4.0)) * 4)
    dy = y2 - y1
    dx1 = mid_x - x1
    dx2 = x2 - mid_x

    sign_x1 = 1.0 if dx1 > 0 else -1.0
    sign_x2 = 1.0 if dx2 > 0 else -1.0
    sign_y = 1.0 if dy > 0 else -1.0

    actual_r = min(r, abs(dx1) / 2.0, abs(dx2) / 2.0, abs(dy) / 2.0)
    if actual_r < 1.0:
        return (
            f"M {x1:.1f} {y1:.1f} L {mid_x:.1f} {y1:.1f} L {mid_x:.1f} {y2:.1f} L {x2:.1f} {y2:.1f}"
        )
    r_str = "8" if abs(actual_r - 8.0) < 0.01 else f"{actual_r:.1f}"

    # First turn at (mid_x, y1), turn towards y2
    turn1_x = mid_x - (sign_x1 * actual_r)
    sweep1 = 1 if (sign_x1 * sign_y > 0) else 0

    # Second turn at (mid_x, y2), turn towards x2
    turn2_y = y2 - (sign_y * actual_r)
    sweep2 = 0 if (sign_x2 * sign_y > 0) else 1

    return (
        f"M {x1:.1f} {y1:.1f} "
        f"L {turn1_x:.1f} {y1:.1f} "
        f"A {r_str} {r_str} 0 0 {sweep1} {mid_x:.1f} {(y1 + sign_y * actual_r):.1f} "
        f"L {mid_x:.1f} {turn2_y:.1f} "
        f"A {r_str} {r_str} 0 0 {sweep2} {(mid_x + sign_x2 * actual_r):.1f} {y2:.1f} "
        f"L {x2:.1f} {y2:.1f}"
    )


_NODE_KIND_STYLES: dict[str, tuple[str, str, str, str, str, str]] = {
    "external": ("#F0F9FF", "#0284C7", "rgba(2, 132, 199, 0.12)", "#0284C7", "#0369A1", "#0C4A6E"),
    "client": ("#F0F9FF", "#0284C7", "rgba(2, 132, 199, 0.12)", "#0284C7", "#0369A1", "#0C4A6E"),
    "stream": ("#F5F3FF", "#7C3AED", "rgba(124, 58, 237, 0.12)", "#7C3AED", "#6D28D9", "#4C1D95"),
    "security": ("#F5F3FF", "#7C3AED", "rgba(124, 58, 237, 0.12)", "#7C3AED", "#6D28D9", "#4C1D95"),
    "datastore": ("#FFFBEB", "#D97706", "rgba(217, 119, 6, 0.12)", "#D97706", "#B45309", "#78350F"),
    "store": ("#FFFBEB", "#D97706", "rgba(217, 119, 6, 0.12)", "#D97706", "#B45309", "#78350F"),
    "target": ("#FFFBEB", "#D97706", "rgba(217, 119, 6, 0.12)", "#D97706", "#B45309", "#78350F"),
    "model": ("#ECFDF5", "#059669", "rgba(5, 150, 105, 0.12)", "#059669", "#047857", "#064E3B"),
    "ai": ("#ECFDF5", "#059669", "rgba(5, 150, 105, 0.12)", "#059669", "#047857", "#064E3B"),
    "gateway": ("#EFF6FF", "#2563EB", "rgba(37, 99, 235, 0.12)", "#2563EB", "#1D4ED8", "#1E3A8A"),
    "surface": ("#EFF6FF", "#2563EB", "rgba(37, 99, 235, 0.12)", "#2563EB", "#1D4ED8", "#1E3A8A"),
    "step": ("#EFF6FF", "#2563EB", "rgba(37, 99, 235, 0.12)", "#2563EB", "#1D4ED8", "#1E3A8A"),
}


def _get_node_colors(
    node: DiagramNode, theme: dict[str, str]
) -> tuple[str, str, str, str, str, str]:
    is_dark = _is_dark_hex(theme.get("paper", "#f5f5f5"))
    primary = theme.get("primary", "#3464FD")
    accent = theme.get("accent", "#72F5FA")
    ink = theme.get("ink", "#F4F6FB" if is_dark else "#0F172A")
    surface = theme.get("surface", "#0A182A" if is_dark else "#FFFFFF")

    if is_dark:
        if node.kind == "focal":
            return (
                "#0E223B",
                primary,
                "rgba(52, 100, 253, 0.18)",
                accent,
                accent,
                ink,
            )
        dark_styles: dict[str, tuple[str, str, str, str, str, str]] = {
            "external": (surface, "#38BDF8", "rgba(56, 189, 248, 0.14)", "#38BDF8", accent, ink),
            "client": (surface, "#38BDF8", "rgba(56, 189, 248, 0.14)", "#38BDF8", accent, ink),
            "stream": (surface, "#AE44D5", "rgba(174, 68, 213, 0.16)", "#AE44D5", "#E9D5FF", ink),
            "security": (surface, "#AE44D5", "rgba(174, 68, 213, 0.16)", "#AE44D5", "#E9D5FF", ink),
            "datastore": (surface, "#FF9C09", "rgba(255, 156, 9, 0.15)", "#FF9C09", "#FDE68A", ink),
            "store": (surface, "#FF9C09", "rgba(255, 156, 9, 0.15)", "#FF9C09", "#FDE68A", ink),
            "target": (surface, "#FF9C09", "rgba(255, 156, 9, 0.15)", "#FF9C09", "#FDE68A", ink),
            "model": (surface, "#1FD8A4", "rgba(31, 216, 164, 0.15)", "#1FD8A4", "#6EE7B7", ink),
            "ai": (surface, "#1FD8A4", "rgba(31, 216, 164, 0.15)", "#1FD8A4", "#6EE7B7", ink),
            "gateway": (surface, primary, "rgba(52, 100, 253, 0.16)", primary, accent, ink),
            "surface": (surface, primary, "rgba(52, 100, 253, 0.16)", primary, accent, ink),
            "step": (surface, primary, "rgba(52, 100, 253, 0.16)", primary, accent, ink),
        }
        if node.kind in dark_styles:
            return dark_styles[node.kind]
        return (surface, theme.get("rule", "#1B2A3E"), "transparent", ink, ink, ink)

    if node.kind == "focal":
        return (
            "rgba(52, 100, 253, 0.05)",
            primary,
            "rgba(52, 100, 253, 0.12)",
            primary,
            primary,
            ink,
        )
    if node.kind in _NODE_KIND_STYLES:
        return _NODE_KIND_STYLES[node.kind]
    return (surface, ink, "transparent", ink, ink, ink)


def _compute_edge_endpoints(
    src: DiagramNode, tgt: DiagramNode, edge: DiagramEdge
) -> tuple[float, float, float, float]:
    src_port = getattr(edge, "attach_source", "right") or "right"
    tgt_port = getattr(edge, "attach_target", "left") or "left"

    if src_port == "bottom":
        x1, y1 = src.x + (src.width / 2.0), src.y + src.height
    elif src_port == "top":
        x1, y1 = src.x + (src.width / 2.0), src.y
    elif src_port == "left":
        x1, y1 = src.x, src.y + (src.height / 2.0)
    else:
        x1, y1 = src.x + src.width, src.y + (src.height / 2.0)

    if tgt_port == "top":
        x2, y2 = tgt.x + (tgt.width / 2.0), tgt.y
    elif tgt_port == "bottom":
        x2, y2 = tgt.x + (tgt.width / 2.0), tgt.y + tgt.height
    elif tgt_port == "right":
        x2, y2 = tgt.x + tgt.width, tgt.y + (tgt.height / 2.0)
    else:
        x2, y2 = tgt.x, tgt.y + (tgt.height / 2.0)

    return x1, y1, x2, y2


def _render_svg_groups(
    groups: list[DiagramGroup], theme: dict[str, str], *, ready: bool = False
) -> list[str]:
    out: list[str] = []
    for grp in groups:
        parts: list[str] = []
        gx = float(int(round(grp.x / 4.0)) * 4)
        gy = float(int(round(grp.y / 4.0)) * 4)
        gw = float(max(100, int(round(grp.width / 4.0)) * 4))
        gh = float(max(60, int(round(grp.height / 4.0)) * 4))

        grp_stroke, grp_fill, grp_surface = (
            _ar.group_paints()
            if ready
            else (theme.get("primary", "#3464FD"), "rgba(52, 100, 253, 0.03)", theme["surface"])
        )

        # In animation_ready output the id moves from the rect to the wrapping <g>.
        id_attr = "" if ready else f'data-group-id="{_escape(grp.id)}" '
        parts.append(
            f'  <rect {id_attr}x="{gx:.1f}" y="{gy:.1f}" '
            f'width="{gw:.1f}" height="{gh:.1f}" rx="12" '
            f"{paint(('fill', grp_fill), ('stroke', grp_stroke))} "
            f'stroke-width="1.5" stroke-dasharray="6,4"/>'
        )

        if grp.label:
            escaped_grp_label = _escape(grp.label.upper())
            lbl_w = max(80.0, len(grp.label.strip()) * 6.6 + 24.0)
            lbl_h = 20.0
            lbl_x = gx + 16.0
            lbl_y = gy - 10.0 if gy >= 10 else gy + 6.0

            parts.append(
                f'  <rect x="{lbl_x:.1f}" y="{lbl_y:.1f}" width="{lbl_w:.1f}" height="{lbl_h:.1f}" rx="4" '
                f'{paint(("fill", grp_surface), ("stroke", grp_stroke))} stroke-width="1.2"/>'
            )
            parts.append(
                f'  <text x="{(lbl_x + lbl_w / 2.0):.1f}" y="{(lbl_y + 13.5):.1f}" '
                f"{paint(('fill', grp_stroke))} "
                f'font-size="8.5" font-weight="700" font-family="{theme["font_mono"]}" '
                f'text-anchor="middle" letter-spacing="0.08em">{escaped_grp_label}</text>'
            )
        out.extend(_ar.wrap(parts, "data-group-id", _escape(grp.id), ready))
    return out


def _render_svg_edges(
    edges: list[DiagramEdge],
    node_map: dict[str, DiagramNode],
    theme: dict[str, str],
    *,
    ready: bool = False,
) -> list[str]:
    out: list[str] = []
    for edge in edges:
        parts: list[str] = []
        src = node_map.get(edge.source)
        tgt = node_map.get(edge.target)
        if not src or not tgt:
            continue

        x1, y1, x2, y2 = _compute_edge_endpoints(src, tgt, edge)
        path_d = _compute_orthogonal_elbow(x1, y1, x2, y2, r=8.0)
        marker = (
            "url(#arrow-accent)"
            if edge.kind == "accent"
            else ("url(#arrow-primary)" if edge.kind == "primary" else "url(#arrow)")
        )
        stroke_color: str | Paint = (
            theme["accent"]
            if edge.kind == "accent"
            else (theme.get("primary", "#3464FD") if edge.kind == "primary" else theme["muted"])
        )
        lbl_fill, lbl_stroke, lbl_text = theme["surface"], theme["rule"], theme["ink"]
        if ready:
            stroke_color, lbl_fill, lbl_stroke, lbl_text = _ar.edge_paints(edge.kind)

        # In animation_ready output the edge key moves from the path to the wrapping <g>.
        edge_attr = "" if ready else f'data-edge="{_escape(edge.source)}->{_escape(edge.target)}" '
        parts.append(
            f'  <path d="{path_d}" {edge_attr}'
            f"{paint(('fill', 'none'), ('stroke', stroke_color))} "
            f'stroke-width="1.5" marker-end="{marker}"/>'
        )

        if edge.label:
            mid_x = (x1 + x2) / 2.0
            mid_y = (y1 + y2) / 2.0
            clean_edge_label = edge.label.replace("\r", "").replace("\n", " ")
            escaped_edge_label = _escape(clean_edge_label)
            lbl_w = max(40.0, len(clean_edge_label) * 6.6 + 16.0)
            lbl_h = 16.0
            rect_y = mid_y - (lbl_h / 2.0)
            text_y = mid_y + 3.5

            parts.append(
                f'  <rect x="{mid_x - (lbl_w / 2.0):.1f}" y="{rect_y:.1f}" '
                f'width="{lbl_w:.1f}" height="{lbl_h:.1f}" rx="3" '
                f'{paint(("fill", lbl_fill), ("stroke", lbl_stroke))} stroke-width="0.8"/>'
            )
            parts.append(
                f'  <text x="{mid_x:.1f}" y="{text_y:.1f}" {paint(("fill", lbl_text))} '
                f'font-size="8" font-weight="500" font-family="{theme["font_mono"]}" text-anchor="middle" '
                f'letter-spacing="0.04em">{escaped_edge_label}</text>'
            )
        out.extend(_ar.wrap(parts, "data-edge", _escape(f"{edge.source}->{edge.target}"), ready))
    return out


def _render_svg_nodes(
    nodes: list[DiagramNode], theme: dict[str, str], *, ready: bool = False
) -> list[str]:
    out: list[str] = []
    for node in nodes:
        parts: list[str] = []
        escaped_label = _escape(node.label)
        escaped_sublabel = _escape(node.sublabel)
        colors: tuple[str | Paint, ...] = (
            _ar.node_paints(node) if ready else _get_node_colors(node, theme)
        )
        fill_color, stroke_color, chip_bg, chip_stroke, chip_text, text_color = colors
        sub_color: str | Paint = role("muted") if ready else theme["muted"]

        # 1. Base styled box (in animation_ready output the id moves to the wrapping <g>)
        id_attr = "" if ready else f'data-node-id="{_escape(node.id)}" '
        parts.append(
            f'  <rect {id_attr}x="{node.x:.1f}" y="{node.y:.1f}" '
            f'width="{node.width:.1f}" height="{node.height:.1f}" rx="8" '
            f'{paint(("fill", fill_color), ("stroke", stroke_color))} stroke-width="1.4"/>'
        )

        # 2. Tag chip
        has_chip = node.kind not in ("step", "") and node.height >= 56
        if has_chip:
            tag_text = _escape(node.kind.upper())
            chip_w = max(34.0, len(tag_text) * 5.5 + 10.0)
            parts.append(
                f'  <rect x="{(node.x + 10.0):.1f}" y="{(node.y + 8.0):.1f}" width="{chip_w:.1f}" height="13" rx="3" '
                f'{paint(("fill", chip_bg), ("stroke", chip_stroke))} stroke-width="0.8"/>'
            )
            parts.append(
                f'  <text x="{(node.x + 10.0 + chip_w / 2.0):.1f}" y="{(node.y + 17.5):.1f}" '
                f"{paint(('fill', chip_text))} "
                f'font-size="7" font-weight="600" font-family="{theme["font_mono"]}" text-anchor="middle" '
                f'letter-spacing="0.08em">{tag_text}</text>'
            )

        # 3. Label text
        cx = node.x + (node.width / 2.0)
        cy = node.y + (node.height / 2.0)
        label_y = (
            (cy + 2.0 if not node.sublabel else cy - 1.0)
            if has_chip
            else (cy + 3.0 if not node.sublabel else cy - 4.0)
        )
        sub_y = (cy + 14.0) if has_chip else (cy + 11.0)

        parts.append(
            f'  <text x="{cx:.1f}" y="{label_y:.1f}" {paint(("fill", text_color))} '
            f'font-size="11.5" font-weight="600" font-family="{theme["font_sans"]}" '
            f'text-anchor="middle">{escaped_label}</text>'
        )

        # 4. Sublabel text
        if node.sublabel:
            parts.append(
                f'  <text x="{cx:.1f}" y="{sub_y:.1f}" {paint(("fill", sub_color))} '
                f'font-size="8.5" font-family="{theme["font_mono"]}" '
                f'text-anchor="middle">{escaped_sublabel}</text>'
            )
        out.extend(_ar.wrap(parts, "data-node-id", _escape(node.id), ready))
    return out


def render_svg(
    ir: DiagramIR,
    profiles_root: Path | None = None,
    profile: str | None = None,
    product: str | None = None,
    *,
    animation_ready: bool = False,
) -> str:
    """Render a DiagramIR into an accessible, publication-grade SVG.

    ``animation_ready=True``: see the contract in ``gtm_core.diagrams.animation_ready``.
    """
    ready = animation_ready
    if ready:
        ir = _ar.prepare(ir)
    raw_theme = resolve_diagram_theme(profiles_root, profile, product)
    theme = {k: _escape(v) for k, v in raw_theme.items()}
    safe_slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", ir.slug or "diagram").strip("-") or "diagram"
    nodes = list(ir.nodes)
    edges = list(ir.edges)

    _layout_nodes_4px_grid(nodes, edges, ir.direction)

    # Calculate bounding box (including groups)
    group_max_x = [g.x + g.width for g in ir.groups] if ir.groups else []
    group_max_y = [g.y + g.height for g in ir.groups] if ir.groups else []
    max_x = max([n.x + n.width for n in nodes] + group_max_x + [800.0])
    max_y = max([n.y + n.height for n in nodes] + group_max_y + [400.0])
    vb_w = int(math.ceil((max_x + 60.0) / 4.0) * 4)
    vb_h = int(math.ceil((max_y + 80.0) / 4.0) * 4)

    node_map = {n.id: n for n in nodes}

    title_id = f"{safe_slug}-title"
    desc_id = f"{safe_slug}-desc"
    escaped_title = _escape(re.sub(r"[\[\]]", "", ir.title))
    escaped_desc = _escape(ir.description)

    if ready:
        root_open = _ar.svg_open(vb_w, vb_h, title_id, desc_id)
        m_muted, m_accent, m_primary, m_link, bg = _ar.header_fills()
    else:
        root_open = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vb_w} {vb_h}" '
            f'role="img" aria-labelledby="{title_id} {desc_id}">'
        )
        m_muted = f'fill="{theme["muted"]}"'
        m_accent = f'fill="{theme["accent"]}"'
        m_primary = f'fill="{theme.get("primary", "#3464FD")}"'
        m_link = f'fill="{theme["link"]}"'
        bg = f'fill="{theme["paper"]}"'

    svg_parts: list[str] = [
        root_open,
        f'  <title id="{title_id}">{escaped_title}</title>',
        f'  <desc id="{desc_id}">{escaped_desc}</desc>',
        "  <defs>",
        '    <marker id="arrow" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">',
        f'      <polygon points="0 0, 8 3, 0 6" {m_muted}/>',
        "    </marker>",
        '    <marker id="arrow-accent" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">',
        f'      <polygon points="0 0, 8 3, 0 6" {m_accent}/>',
        "    </marker>",
        '    <marker id="arrow-primary" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">',
        f'      <polygon points="0 0, 8 3, 0 6" {m_primary}/>',
        "    </marker>",
        '    <marker id="arrow-link" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">',
        f'      <polygon points="0 0, 8 3, 0 6" {m_link}/>',
        "    </marker>",
        "  </defs>",
        f'  <rect width="100%" height="100%" {bg}/>',
    ]

    svg_parts.extend(_render_svg_groups(ir.groups, theme, ready=ready))
    svg_parts.extend(_render_svg_edges(edges, node_map, theme, ready=ready))
    svg_parts.extend(_render_svg_nodes(nodes, theme, ready=ready))
    svg_parts.append("</svg>")
    return "\n".join(svg_parts)


def render_html(
    ir: DiagramIR,
    profiles_root: Path | None = None,
    profile: str | None = None,
    product: str | None = None,
) -> str:
    """Render DiagramIR inside a full, self-contained editorial HTML presentation."""
    svg = render_svg(ir, profiles_root, profile, product)
    theme = resolve_diagram_theme(profiles_root, profile, product)
    escaped_title = _escape(re.sub(r"[\[\]]", "", ir.title))
    heading_html = re.sub(
        r"\[([^\[\]]+)\]",
        r'<span class="payoff">\1</span>',
        _escape(ir.title),
    )
    escaped_desc = _escape(ir.description)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escaped_title}</title>
  <style>
    :root {{
      --paper: {theme["paper"]};
      --surface: {theme["surface"]};
      --ink: {theme["ink"]};
      --muted: {theme["muted"]};
      --primary: {theme.get("primary", "#3464FD")};
      --accent: {theme["accent"]};
      --rule: {theme["rule"]};
      --font-display: {theme["font_display"]};
      --font-sans: {theme["font_sans"]};
      --font-mono: {theme["font_mono"]};
    }}
    body {{
      margin: 0;
      padding: 2.5rem 1.5rem;
      background-color: var(--paper);
      color: var(--ink);
      font-family: var(--font-sans);
      -webkit-font-smoothing: antialiased;
    }}
    .container {{
      max-width: 1040px;
      margin: 0 auto;
    }}
    header {{
      margin-bottom: 2rem;
    }}
    .eyebrow {{
      font-family: var(--font-mono);
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--accent);
      margin: 0 0 0.5rem 0;
    }}
    h1 {{
      font-family: var(--font-display);
      font-size: 2.25rem;
      font-weight: 600;
      letter-spacing: -0.025em;
      margin: 0 0 0.5rem 0;
    }}
    h1 .payoff {{
      background: linear-gradient(90deg, var(--primary), var(--accent));
      -webkit-background-clip: text;
      background-clip: text;
      color: transparent;
      -webkit-text-fill-color: transparent;
    }}
    p.desc {{
      color: var(--muted);
      margin: 0;
      font-size: 1rem;
    }}
    .diagram-frame {{
      background: var(--surface);
      border: 1px solid var(--rule);
      border-radius: 8px;
      padding: 1.5rem;
      overflow-x: auto;
      margin-bottom: 2rem;
    }}
    .card-grid {{
      display: grid;
      grid-template-columns: 1.1fr 1fr 0.9fr;
      gap: 1rem;
      margin-top: 2rem;
    }}
    .card {{
      background: var(--surface);
      border: 1px solid var(--rule);
      border-radius: 6px;
      padding: 1.25rem;
    }}
    .card-header {{
      display: flex;
      align-items: center;
      gap: 0.5rem;
      margin-bottom: 0.5rem;
    }}
    .card-dot {{
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--accent);
    }}
    .card h3 {{
      margin: 0;
      font-size: 0.95rem;
      font-weight: 600;
    }}
    footer {{
      margin-top: 3rem;
      padding-top: 1rem;
      border-top: 1px solid var(--rule);
      font-family: var(--font-mono);
      font-size: 0.75rem;
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <p class="eyebrow">SYSTEM ARCHITECTURE</p>
      <h1>{heading_html}</h1>
      <p class="desc">{escaped_desc}</p>
    </header>
    <div class="diagram-frame">
      {svg}
    </div>
    <div class="card-grid">
      <div class="card">
        <div class="card-header">
          <span class="card-dot"></span>
          <h3>Components</h3>
        </div>
        <p class="desc">Verified against editorial architecture standards.</p>
      </div>
      <div class="card">
        <div class="card-header">
          <span class="card-dot"></span>
          <h3>Topology</h3>
        </div>
        <p class="desc">4px grid alignment with orthogonal routing.</p>
      </div>
      <div class="card">
        <div class="card-header">
          <span class="card-dot"></span>
          <h3>Brand Fidelity</h3>
        </div>
        <p class="desc">Mapped to active profile tokens.</p>
      </div>
    </div>
    <footer>
      <span>Generated by Diagram Design • GTM Content OS</span>
    </footer>
  </div>
</body>
</html>
"""
