"""Brandkit integration and fallback tests for diagram rendering.

PRD §5.2: Palette Resolution & Fallback Verification.
Assert that rendered SVG uses hex codes derived from the active PROFILE.md/BRAND.toml,
and missing tenant tokens fall back to base BRAND.toml safely.
"""

from __future__ import annotations

from pathlib import Path

from gtm_core.diagrams.ir import DiagramIR, DiagramNode
from gtm_core.diagrams.render import render_svg, resolve_diagram_theme


def test_theme_resolution_with_brandkit(tmp_path: Path):
    """Test palette extraction from a mock profile's BRAND.toml."""
    profile_dir = tmp_path / "profiles" / "acme" / "knowledge"
    profile_dir.mkdir(parents=True)
    brand_toml = profile_dir / "BRAND.toml"
    brand_toml.write_text(
        """
[palette]
canvas = "#121212"
surface = "#1e1e1e"
ink = "#f0f0f0"
primary = "#ff5500"
accent = "#00ccff"
rule = "#333333"
""",
        encoding="utf-8",
    )

    theme = resolve_diagram_theme(profiles_root=tmp_path / "profiles", profile="acme")
    assert theme["paper"] == "#121212"
    assert theme["surface"] == "#1e1e1e"
    assert theme["ink"] == "#f0f0f0"
    assert theme["accent"] == "#00ccff"
    assert theme["rule"] == "#333333"


def test_theme_resolution_fallback_to_defaults():
    """Ensure missing profile or absent brandkit keys fall back gracefully to design system defaults."""
    theme = resolve_diagram_theme(profiles_root=Path("/nonexistent"), profile="unknown")
    # Default editorial palette
    assert theme["paper"] == "#f5f5f5"
    assert theme["ink"] == "#2d3142"
    assert theme["accent"] == "#eb6c36"
    assert theme["muted"] == "#4f5d75"


def test_rendered_svg_contains_brand_hexes(tmp_path: Path):
    """Verify that SVG attributes use the resolved brand hexes."""
    profile_dir = tmp_path / "profiles" / "brandcorp" / "knowledge"
    profile_dir.mkdir(parents=True)
    (profile_dir / "BRAND.toml").write_text(
        """
[palette]
canvas = "#fafafa"
ink = "#111827"
accent = "#e11d48"
""",
        encoding="utf-8",
    )

    ir = DiagramIR(
        title="Branded Diagram",
        nodes=[DiagramNode(id="x", label="Core Engine", kind="focal")],
    )

    svg = render_svg(ir, profiles_root=tmp_path / "profiles", profile="brandcorp")
    assert "#fafafa" in svg
    assert "#e11d48" in svg
