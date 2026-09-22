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

from .ir import DiagramEdge, DiagramIR, DiagramNode


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
}


def resolve_diagram_theme(
    profiles_root: Path | None = None,
    profile: str | None = None,
    product: str | None = None,
) -> dict[str, str]:
    """Resolve brand tokens from the active profile's brandkit with safe defaults."""
    theme = dict(_DEFAULT_THEME)
    if not profile:
        return theme

    root = profiles_root if profiles_root is not None else Path("profiles")
    try:
        from gtm_core.brandkit import load_brand_kit

        kit = load_brand_kit(root, profile, product)
        palette = kit.get("palette", {}) if isinstance(kit, dict) else {}
        if isinstance(palette, dict):
            if "canvas_light" in palette:
                theme["paper"] = str(palette["canvas_light"])
            elif "canvas" in palette:
                theme["paper"] = str(palette["canvas"])

            if "surface_light" in palette:
                theme["surface"] = str(palette["surface_light"])
            elif "surface" in palette:
                theme["surface"] = str(palette["surface"])

            if "ink_light" in palette:
                theme["ink"] = str(palette["ink_light"])
            elif "ink" in palette:
                theme["ink"] = str(palette["ink"])

            if "primary" in palette:
                theme["primary"] = str(palette["primary"])
            if "accent" in palette:
                theme["accent"] = str(palette["accent"])
            if "secondary" in palette:
                theme["muted"] = str(palette["secondary"])
            if "rule_light" in palette:
                theme["rule"] = str(palette["rule_light"])
            elif "rule" in palette:
                theme["rule"] = str(palette["rule"])
    except Exception:  # nosec B110
        # Fall back gracefully to base defaults
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


def render_svg(
    ir: DiagramIR,
    profiles_root: Path | None = None,
    profile: str | None = None,
    product: str | None = None,
) -> str:
    """Render a DiagramIR into an accessible, publication-grade SVG."""
    raw_theme = resolve_diagram_theme(profiles_root, profile, product)
    theme = {k: _escape(v) for k, v in raw_theme.items()}
    safe_slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", ir.slug or "diagram").strip("-") or "diagram"
    nodes = list(ir.nodes)
    edges = list(ir.edges)

    _layout_nodes_4px_grid(nodes, edges, ir.direction)

    # Calculate bounding box
    max_x = max([n.x + n.width for n in nodes] + [800.0])
    max_y = max([n.y + n.height for n in nodes] + [400.0])
    vb_w = int(math.ceil((max_x + 60.0) / 4.0) * 4)
    vb_h = int(math.ceil((max_y + 80.0) / 4.0) * 4)

    node_map = {n.id: n for n in nodes}

    title_id = f"{safe_slug}-title"
    desc_id = f"{safe_slug}-desc"
    escaped_title = _escape(ir.title)
    escaped_desc = _escape(ir.description)

    svg_parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vb_w} {vb_h}" '
        f'role="img" aria-labelledby="{title_id} {desc_id}">',
        f'  <title id="{title_id}">{escaped_title}</title>',
        f'  <desc id="{desc_id}">{escaped_desc}</desc>',
        "  <defs>",
        '    <marker id="arrow" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">',
        f'      <polygon points="0 0, 8 3, 0 6" fill="{theme["muted"]}"/>',
        "    </marker>",
        '    <marker id="arrow-accent" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">',
        f'      <polygon points="0 0, 8 3, 0 6" fill="{theme["accent"]}"/>',
        "    </marker>",
        '    <marker id="arrow-link" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">',
        f'      <polygon points="0 0, 8 3, 0 6" fill="{theme["link"]}"/>',
        "    </marker>",
        "  </defs>",
        f'  <rect width="100%" height="100%" fill="{theme["paper"]}"/>',
    ]

    # Render Connectors before nodes (z-order: lines behind boxes)
    for edge in edges:
        src = node_map.get(edge.source)
        tgt = node_map.get(edge.target)
        if not src or not tgt:
            continue

        x1 = src.x + src.width
        y1 = src.y + (src.height / 2.0)
        x2 = tgt.x
        y2 = tgt.y + (tgt.height / 2.0)

        path_d = _compute_orthogonal_elbow(x1, y1, x2, y2, r=8.0)
        marker = "url(#arrow-accent)" if edge.kind == "accent" else "url(#arrow)"
        stroke_color = theme["accent"] if edge.kind == "accent" else theme["muted"]

        svg_parts.append(
            f'  <path d="{path_d}" data-edge="{_escape(edge.source)}->{_escape(edge.target)}" '
            f'fill="none" stroke="{stroke_color}" stroke-width="1.5" marker-end="{marker}"/>'
        )

        if edge.label:
            mid_x = (x1 + x2) / 2.0
            mid_y = (y1 + y2) / 2.0
            clean_edge_label = edge.label.replace("\r", "").replace("\n", " ")
            escaped_edge_label = _escape(clean_edge_label)
            lbl_w = max(40.0, len(clean_edge_label) * 7.0 + 12.0)
            lbl_h = 14.0
            # 6-10px visible margin above connector stroke
            rect_y = mid_y - 20.0
            text_y = rect_y + 10.0

            svg_parts.append(
                f'  <rect x="{mid_x - (lbl_w / 2.0):.1f}" y="{rect_y:.1f}" '
                f'width="{lbl_w:.1f}" height="{lbl_h:.1f}" rx="2" fill="{theme["paper"]}"/>'
            )
            svg_parts.append(
                f'  <text x="{mid_x:.1f}" y="{text_y:.1f}" fill="{theme["soft"]}" '
                f'font-size="8" font-family="\'Geist Mono\', monospace" text-anchor="middle" '
                f'letter-spacing="0.06em">{escaped_edge_label}</text>'
            )

    # Render Nodes
    for node in nodes:
        escaped_label = _escape(node.label)
        escaped_sublabel = _escape(node.sublabel)
        is_focal = node.kind == "focal"

        fill_color = theme["surface"]
        stroke_color = theme["accent"] if is_focal else theme["ink"]
        text_color = theme["accent"] if is_focal else theme["ink"]

        # 1. Base styled box
        svg_parts.append(
            f'  <rect data-node-id="{_escape(node.id)}" x="{node.x:.1f}" y="{node.y:.1f}" '
            f'width="{node.width:.1f}" height="{node.height:.1f}" rx="6" '
            f'fill="{fill_color}" stroke="{stroke_color}" stroke-width="1.2"/>'
        )

        # 2. Tag chip
        tag_text = _escape(node.kind.upper())
        svg_parts.append(
            f'  <rect x="{(node.x + 8.0):.1f}" y="{(node.y + 6.0):.1f}" width="34" height="12" rx="2" '
            f'fill="transparent" stroke="{stroke_color}" stroke-width="0.8" opacity="0.6"/>'
        )
        svg_parts.append(
            f'  <text x="{(node.x + 25.0):.1f}" y="{(node.y + 15.0):.1f}" fill="{stroke_color}" '
            f'font-size="7" font-family="\'Geist Mono\', monospace" text-anchor="middle" '
            f'letter-spacing="0.08em">{tag_text}</text>'
        )

        # 3. Label text
        cx = node.x + (node.width / 2.0)
        cy = node.y + (node.height / 2.0)
        label_y = cy + 2.0 if not node.sublabel else cy - 4.0

        svg_parts.append(
            f'  <text x="{cx:.1f}" y="{label_y:.1f}" fill="{text_color}" '
            f'font-size="12" font-weight="600" font-family="\'Geist\', sans-serif" '
            f'text-anchor="middle">{escaped_label}</text>'
        )

        # 4. Sublabel text
        if node.sublabel:
            svg_parts.append(
                f'  <text x="{cx:.1f}" y="{(cy + 14.0):.1f}" fill="{theme["muted"]}" '
                f'font-size="9" font-family="\'Geist Mono\', monospace" '
                f'text-anchor="middle">{escaped_sublabel}</text>'
            )

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
    escaped_title = _escape(ir.title)
    escaped_desc = _escape(ir.description)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escaped_title}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500;600&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
  <style>
    :root {{
      --paper: {theme["paper"]};
      --surface: {theme["surface"]};
      --ink: {theme["ink"]};
      --muted: {theme["muted"]};
      --accent: {theme["accent"]};
      --rule: {theme["rule"]};
    }}
    body {{
      margin: 0;
      padding: 2.5rem 1.5rem;
      background-color: var(--paper);
      color: var(--ink);
      font-family: 'Geist', sans-serif;
    }}
    .container {{
      max-width: 1040px;
      margin: 0 auto;
    }}
    header {{
      margin-bottom: 2rem;
    }}
    .eyebrow {{
      font-family: 'Geist Mono', monospace;
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--accent);
      margin: 0 0 0.5rem 0;
    }}
    h1 {{
      font-family: 'Instrument Serif', Georgia, serif;
      font-size: 2.5rem;
      font-weight: 400;
      margin: 0 0 0.5rem 0;
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
      font-family: 'Geist Mono', monospace;
      font-size: 0.75rem;
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <p class="eyebrow">SYSTEM ARCHITECTURE</p>
      <h1>{escaped_title}</h1>
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
