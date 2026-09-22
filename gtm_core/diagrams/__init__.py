"""Diagram Design native SVG/HTML generation engine."""

from __future__ import annotations

from .ir import DiagramEdge, DiagramGroup, DiagramIR, DiagramNode
from .render import render_html, render_svg, resolve_diagram_theme

__all__ = [
    "DiagramEdge",
    "DiagramGroup",
    "DiagramIR",
    "DiagramNode",
    "render_html",
    "render_svg",
    "resolve_diagram_theme",
]
