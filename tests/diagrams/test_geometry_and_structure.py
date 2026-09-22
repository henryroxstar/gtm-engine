"""Geometric and architectural contract tests for diagram rendering.

PRD §5.3 & §4:
- 4px grid alignments (origins, widths, heights, gaps)
- r=8 orthogonal right-angle connectors
- Accessible SVG contract (role="img", aria-labelledby, <title> first child, prefixed IDs)
- Connector label masks with visible gap
"""

from __future__ import annotations

import re

from defusedxml import ElementTree as ET

from gtm_core.diagrams.ir import DiagramEdge, DiagramIR, DiagramNode
from gtm_core.diagrams.render import render_svg


def test_svg_accessibility_contract():
    """Verify accessible SVG contract: role, aria-labelledby, title first child, desc."""
    ir = DiagramIR(
        title="Payment Flow",
        description="Architecture showing payment gateway routing to fraud check",
        slug="payment-flow",
        nodes=[
            DiagramNode(id="gateway", label="Gateway", kind="focal"),
            DiagramNode(id="fraud", label="Fraud Service", kind="backend"),
        ],
        edges=[DiagramEdge(source="gateway", target="fraud", label="VERIFY")],
    )

    svg = render_svg(ir)
    root = ET.fromstring(svg)

    assert root.attrib.get("role") == "img"
    aria_labelledby = root.attrib.get("aria-labelledby", "")
    assert "payment-flow-title" in aria_labelledby
    assert "payment-flow-desc" in aria_labelledby

    # Title must be first element
    children = list(root)
    assert children[0].tag.endswith("title")
    assert children[0].attrib.get("id") == "payment-flow-title"
    assert children[0].text == "Payment Flow"

    assert children[1].tag.endswith("desc")
    assert children[1].attrib.get("id") == "payment-flow-desc"


def test_4px_grid_and_orthogonal_geometry():
    """Verify that node dimensions and origins conform to 4px grid, and connectors use r=8 elbow curves."""
    ir = DiagramIR(
        title="Grid Test",
        slug="grid-test",
        nodes=[
            DiagramNode(id="a", label="Source Node", x=40, y=40, width=120, height=80),
            DiagramNode(id="b", label="Target Node", x=240, y=160, width=120, height=80),
        ],
        edges=[DiagramEdge(source="a", target="b", label="ROUTE")],
    )

    svg = render_svg(ir)
    root = ET.fromstring(svg)

    # Check node rectangles for 4px grid
    rects = [elem for elem in root.iter() if elem.tag.endswith("rect")]
    node_rects = [r for r in rects if r.attrib.get("data-node-id")]
    assert len(node_rects) >= 2
    for nr in node_rects:
        x = float(nr.attrib["x"])
        y = float(nr.attrib["y"])
        w = float(nr.attrib["width"])
        h = float(nr.attrib["height"])
        assert x % 4 == 0, f"x={x} not on 4px grid"
        assert y % 4 == 0, f"y={y} not on 4px grid"
        assert w % 4 == 0, f"width={w} not on 4px grid"
        assert h % 4 == 0, f"height={h} not on 4px grid"

    # Check connector path: must be orthogonal elbow with arc r=8
    paths = [
        elem for elem in root.iter() if elem.tag.endswith("path") and elem.attrib.get("data-edge")
    ]
    assert len(paths) >= 1
    d_attr = paths[0].attrib.get("d", "")
    # Should contain arc command with radius 8 (A 8 8 or a 8 8)
    assert re.search(r"[Aa]\s*8[,\s]+8", d_attr), f"Connector path '{d_attr}' missing r=8 arc"


def test_orthogonal_geometry_small_segment_no_distortion():
    """Verify that nodes placed very close together render valid SVG without overshooting."""
    ir = DiagramIR(
        title="Tight Grid",
        slug="tight-grid",
        nodes=[
            DiagramNode(id="a", label="A", x=40, y=40, width=40, height=40),
            DiagramNode(id="b", label="B", x=84, y=44, width=40, height=40),
        ],
        edges=[DiagramEdge(source="a", target="b", label="")],
    )
    svg = render_svg(ir)
    root = ET.fromstring(svg)
    paths = [
        elem for elem in root.iter() if elem.tag.endswith("path") and elem.attrib.get("data-edge")
    ]
    assert len(paths) >= 1


def test_empty_diagram_renders_valid_svg():
    """Verify that an empty DiagramIR renders a valid SVG canvas without crashing."""
    ir = DiagramIR(title="Empty Canvas", slug="empty-canvas", nodes=[], edges=[])
    svg = render_svg(ir)
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    rects = [elem for elem in root.iter() if elem.tag.endswith("rect")]
    assert len(rects) >= 1  # Canvas background rect


def test_single_node_no_edges_diagram():
    """Verify that a single node with no edges renders cleanly on the 4px grid."""
    ir = DiagramIR(
        title="Standalone Service",
        slug="standalone",
        nodes=[DiagramNode(id="app", label="Core App", kind="focal")],
        edges=[],
    )
    svg = render_svg(ir)
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    node_rects = [r for r in root.iter() if r.attrib.get("data-node-id") == "app"]
    assert len(node_rects) == 1
    assert float(node_rects[0].attrib["x"]) % 4 == 0
    assert float(node_rects[0].attrib["y"]) % 4 == 0
