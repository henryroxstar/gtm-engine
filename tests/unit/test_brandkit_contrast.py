"""Tests for WCAG 2.1 relative luminance calculation and contrasting text colors.

Covers Test Plan T1.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core.brandkit_css import render_tokens_css
from gtm_core.design_tokens import contrasting_text_color, relative_luminance

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "profiles"


def test_relative_luminance_extremes_and_shorthand():
    """T1: Relative luminance returns 0.0 for black and 1.0 for white, with 3-digit expansion."""
    assert relative_luminance("#000000") == pytest.approx(0.0)
    assert relative_luminance("#ffffff") == pytest.approx(1.0)
    assert relative_luminance("#000") == pytest.approx(0.0)
    assert relative_luminance("#fff") == pytest.approx(1.0)
    assert relative_luminance("000000") == pytest.approx(0.0)
    assert relative_luminance("ffffff") == pytest.approx(1.0)


def test_contrasting_text_color_boundary_threshold():
    """T1: Contrast threshold L > 0.179 emits #000000, else #ffffff."""
    # Color with L slightly below 0.179 -> #ffffff
    # Color with L slightly above 0.179 -> #000000
    # Let's find exact sRGB grey where L = 0.179:
    # L = ((c + 0.055)/1.055)^2.4 = 0.179 => (c + 0.055)/1.055 = 0.179^(1/2.4) = 0.4879
    # c = 0.4879 * 1.055 - 0.055 = 0.4602 => 0.4602 * 255 = 117.35 => #757575 (L ~ 0.176) vs #767676 (L ~ 0.181)
    l_below = relative_luminance("#757575")
    assert l_below <= 0.179
    assert contrasting_text_color("#757575") == "#ffffff"

    l_above = relative_luminance("#767676")
    assert l_above > 0.179
    assert contrasting_text_color("#767676") == "#000000"


def test_synthetic_brand_kits_contrast_light_and_dark_primaries():
    """T1: Verify light and dark primaries produce legible contrast tokens."""
    # Light primary = #E8E8E8 (L ~ 0.799 > 0.179) -> #000000
    light_kit = {"palette": {"canvas": "#000000", "surface": "#111111", "primary": "#E8E8E8"}}
    assert relative_luminance(light_kit["palette"]["primary"]) > 0.179
    light_css = render_tokens_css(light_kit)
    assert "--text-on-primary: #000000;" in light_css
    assert "--brand-text-on-primary: var(--text-on-primary);" in light_css

    # Dark primary = #1A365D (L ~ 0.04 <= 0.179) -> #ffffff
    # Bright accent = #72F5FA (L ~ 0.814 > 0.179) -> #000000
    dark_kit = {
        "palette": {
            "canvas": "#0A182A",
            "surface": "#0F2238",
            "primary": "#1A365D",
            "accent": "#72F5FA",
        }
    }
    dark_css = render_tokens_css(dark_kit)
    assert "--text-on-primary: #ffffff;" in dark_css
    assert "--text-on-accent: #000000;" in dark_css
    assert "--brand-text-on-primary: var(--text-on-primary);" in dark_css
    assert "--brand-text-on-accent: var(--text-on-accent);" in dark_css


def test_explicit_text_on_palette_overrides():
    """T1: Explicit text_on_primary/accent overrides computed value when valid hex."""
    custom_kit = {
        "palette": {
            "canvas": "#000000",
            "surface": "#111111",
            "primary": "#E8E8E8",  # would naturally compute #000000
            "accent": "#1D58FC",  # would naturally compute #ffffff
            "text_on_primary": "#112233",
            "text_on_accent": "#aabbcc",
        },
    }
    css = render_tokens_css(custom_kit)
    assert "--text-on-primary: #112233;" in css
    assert "--text-on-accent: #aabbcc;" in css

    # Malformed override falls back to computed contrast
    malformed_kit = {
        "palette": {
            "canvas": "#000000",
            "surface": "#111111",
            "primary": "#E8E8E8",
            "accent": "#1D58FC",
            "text_on_primary": "not-a-hex",
            "text_on_accent": "#xyz",
        },
    }
    css_mal = render_tokens_css(malformed_kit)
    assert "--text-on-primary: #000000;" in css_mal
    assert "--text-on-accent: #ffffff;" in css_mal
