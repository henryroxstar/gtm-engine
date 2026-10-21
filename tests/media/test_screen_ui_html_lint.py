"""Unit tests for gtm_core.screen_ui.html_lint."""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core.screen_ui.base import SceneError
from gtm_core.screen_ui.html_lint import (
    check_html_composition,
    check_kinetic_type_fit,
    lint_composition,
)

ROOT = Path(__file__).resolve().parents[2]

_CLEAN_HTML = """<!DOCTYPE html>
<html data-ratio="9:16">
<head>
  <style>
    :root {
      --safe-top: 80px;
      --safe-bottom: 140px;
      --caption-band-top: 1600px;
      color: var(--color-foreground);
      background: var(--color-background);
    }
    .headline {
      font-size: 54px;
      color: #333333;
    }
    .warn-badge {
      color: #F2A93B;
    }
  </style>
</head>
<body>
  <div class="headline">Deterministic Motion</div>
</body>
</html>
"""


def test_html_lint_clean_composition_passes(tmp_path: Path):
    comp = tmp_path / "index.html"
    comp.write_text(_CLEAN_HTML, encoding="utf-8")
    errors = check_html_composition(comp, kit={}, ratio="9:16")
    assert errors == []


def test_html_lint_flags_remote_egress(tmp_path: Path):
    bad_html = _CLEAN_HTML.replace(
        "</head>",
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter">\n</head>',
    )
    comp = tmp_path / "remote.html"
    comp.write_text(bad_html, encoding="utf-8")
    errors = check_html_composition(comp, kit={}, ratio="9:16")
    assert any("H-SEC-01" in e for e in errors)


def test_html_lint_flags_directory_traversal(tmp_path: Path):
    bad_html = _CLEAN_HTML.replace(
        "<body>",
        '<body>\n<img src="../../other-tenant/logo.png">',
    )
    comp = tmp_path / "traversal.html"
    comp.write_text(bad_html, encoding="utf-8")
    errors = check_html_composition(comp, kit={}, ratio="9:16")
    assert any("H-SEC-02" in e for e in errors)


def test_html_lint_flags_blur_repaint_in_keyframes(tmp_path: Path):
    bad_html = _CLEAN_HTML.replace(
        "</style>",
        "@keyframes pulse { 0% { filter: blur(0px); } 100% { filter: blur(10px); } }\n</style>",
    )
    comp = tmp_path / "blur.html"
    comp.write_text(bad_html, encoding="utf-8")
    errors = check_html_composition(comp, kit={}, ratio="9:16")
    assert any("H-PERF-01" in e for e in errors)


def test_html_lint_flags_chromatic_hex(tmp_path: Path):
    bad_html = _CLEAN_HTML.replace(
        "#333333",
        "#FF0044",  # Chromatic red
    )
    comp = tmp_path / "hex.html"
    comp.write_text(bad_html, encoding="utf-8")
    errors = check_html_composition(comp, kit={}, ratio="9:16")
    assert any("H-BRAND-01" in e for e in errors)


def test_html_lint_flags_slop_unless_opted_in(tmp_path: Path):
    slop_html = _CLEAN_HTML.replace(
        "</style>",
        ".glow { box-shadow: 0 0 40px #3bd68a; }\n"
        ".sparkle { opacity: 1; }\n"
        ".grad { -webkit-background-clip: text; }\n"
        "</style>",
    )
    comp = tmp_path / "slop.html"
    comp.write_text(slop_html, encoding="utf-8")

    # Without opt-ins: all 3 flagged
    errors = check_html_composition(comp, kit={}, ratio="9:16")
    assert any("H-SLOP-01" in e for e in errors)
    assert any("H-SLOP-02" in e for e in errors)
    assert any("H-SLOP-03" in e for e in errors)

    # With opt-ins in kit: all 3 allowed
    kit_opted = {
        "motion": {
            "glow_on_focal": True,
            "particles_allowed": True,
            "gradient_text_allowed": True,
        }
    }
    errors_opted = check_html_composition(comp, kit=kit_opted, ratio="9:16")
    assert not any("H-SLOP" in e for e in errors_opted)


def test_html_lint_flags_css_transitions(tmp_path: Path):
    trans_html = _CLEAN_HTML.replace(
        "</style>",
        ".btn { transition: transform 0.2s ease-in-out; }\n</style>",
    )
    comp = tmp_path / "trans.html"
    comp.write_text(trans_html, encoding="utf-8")
    errors = check_html_composition(comp, kit={}, ratio="9:16")
    assert any("H-DET-01" in e for e in errors)


def test_check_kinetic_type_fit():
    # Empty headline
    with pytest.raises(SceneError, match="non-empty --headline"):
        check_kinetic_type_fit({}, kit={}, ratio="9:16")

    # Clean short headline
    check_kinetic_type_fit({"headline": "Agent Security in Production"}, kit={}, ratio="9:16")

    # Giant headline that overflows safe area vertical budget
    long_headline = "This is an extremely long headline that contains way too many words and sentences designed to push right through the caption band threshold"
    with pytest.raises(SceneError, match="exceeding the vertical budget"):
        check_kinetic_type_fit({"headline": long_headline}, kit={}, ratio="9:16")


@pytest.mark.parametrize("scene_name", ["kinetic_type", "hero_reveal", "phone_walkthrough"])
def test_t14_library_scenes_pass_lint_composition(scene_name: str):
    """T14: Built-in library scenes pass lint_composition with zero violations under H-SEC, H-PERF, H-BRAND, H-DET, H-SAFE."""
    scene_dir = ROOT / "gtm_core" / "screen_ui" / "html" / scene_name
    html_file = scene_dir / "index.html"
    assert html_file.is_file(), f"Missing {html_file}"

    errors = lint_composition(
        html_file,
        kit={},
        ratio="9:16",
        is_library_scene=True,
        raise_on_error=False,
    )
    target_rules = ("H-SEC-01", "H-PERF-01", "H-BRAND-01", "H-DET-01", "H-SAFE-01")
    violating = [e for e in errors if any(rule in e for rule in target_rules)]
    assert not violating, f"{scene_name} violated target rules: {violating}"
