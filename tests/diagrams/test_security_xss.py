"""Security and XSS validation tests for diagram rendering.

PRD §5.1: Verify that SVG generation escapes all text labels, titles, and descriptions
to prevent XSS payloads in HTML companion reports.
"""

from __future__ import annotations

from defusedxml import ElementTree as ET

from gtm_core.diagrams.ir import DiagramEdge, DiagramIR, DiagramNode
from gtm_core.diagrams.render import render_html, render_svg


def test_svg_label_xss_escaping():
    """Verify that malicious XSS payloads in node names, sublabels, and edge labels are escaped."""
    xss_payload = '<script>alert("pwned")</script>'
    img_payload = '"><img src=x onerror=alert(1)>'

    ir = DiagramIR(
        title=f"Test {xss_payload}",
        description=f"Desc {img_payload}",
        nodes=[
            DiagramNode(id="n1", label=f"Node 1 {xss_payload}", sublabel=img_payload, kind="focal"),
            DiagramNode(id="n2", label="Node 2 & <tag>", sublabel="api:8080", kind="backend"),
        ],
        edges=[
            DiagramEdge(source="n1", target="n2", label=f"calls {xss_payload}"),
        ],
    )

    svg = render_svg(ir)

    # 1. Output must parse as strictly valid XML (ElementTree would fail if unescaped tags exist)
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")

    # 2. Raw unescaped script tag must NOT exist in the SVG string
    assert "<script>" not in svg
    assert "<img" not in svg
    assert "&lt;script&gt;" in svg
    assert "&quot;pwned&quot;" in svg or '"pwned"' in svg


def test_html_report_xss_escaping():
    """Verify that full editorial HTML output safely escapes all user-supplied content."""
    xss_payload = '<script>alert("html_pwned")</script>'
    ir = DiagramIR(
        title=f"Architecture {xss_payload}",
        description="Safe description",
        nodes=[
            DiagramNode(id="a", label=f"Service {xss_payload}"),
        ],
    )

    html = render_html(ir)
    assert '<script>alert("html_pwned")</script>' not in html
    assert "&lt;script&gt;alert" in html


def test_xml_control_character_stripping():
    """Verify that invalid XML 1.0 control characters (e.g. \x00, \x08, \x1b) are stripped."""
    dirty_label = "Service\x00Name\x08With\x1bEscapes"
    dirty_desc = "Desc\x0cWith\x0eControls"
    ir = DiagramIR(
        title="Valid Title",
        description=dirty_desc,
        nodes=[DiagramNode(id="n1", label=dirty_label)],
        edges=[DiagramEdge(source="n1", target="n1", label="Edge\x07Label")],
    )

    svg = render_svg(ir)
    # Output must parse as valid XML without ParseError
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    assert "\x00" not in svg
    assert "\x08" not in svg
    assert "\x1b" not in svg
    assert "ServiceNameWithEscapes" in svg


def test_slug_sanitization_for_valid_xml_ids():
    """Verify that slugs with spaces, slashes, and symbols yield valid XML IDs and aria-labelledby."""
    ir = DiagramIR(
        title="Complex Title",
        slug="my complex / diagram (v2)!",
        nodes=[DiagramNode(id="n1", label="Service")],
    )

    svg = render_svg(ir)
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")

    aria = root.attrib.get("aria-labelledby", "")
    assert " " in aria  # Two IDs separated by space
    ids = aria.split()
    assert len(ids) == 2
    # Verify no invalid characters in the XML IDs
    for xml_id in ids:
        assert "/" not in xml_id
        assert "(" not in xml_id
        assert ")" not in xml_id
        assert "!" not in xml_id
        assert " " not in xml_id
