"""The one public denied-token scanner (`brandkit.scan_denied`) and its kit reader.

Hex boundary decision: a denied 6-digit hex is a HIT when it stands alone (`#1D58FC`) and when it
is the colour part of an 8-digit `#RRGGBBAA` token (`#1D58FCFF`), because the visible colour is the
denied one — only the alpha differs. It is NOT a hit inside a 7-digit run (`#1D58FC0`) or a longer
one: that is not a colour token at all, so it is not the denied colour.
"""

from __future__ import annotations

import pytest

from gtm_core.brandkit import (
    _scan_text_for_denied_tokens,
    design_system_denied,
    scan_denied,
    validate_design_system_policy,
)

FONTS = ("Oldface",)
HEXES = ("#1D58FC",)


def test_design_system_denied_reads_and_uppercases():
    kit = {"design_system": {"denied_fonts": [" Oldface ", ""], "denied_hexes": ["#1d58fc", " "]}}
    assert design_system_denied(kit) == (("Oldface",), ("#1D58FC",))


@pytest.mark.parametrize(
    "kit",
    [{}, {"design_system": "x"}, {"design_system": {}}, {"design_system": {"denied_fonts": "x"}}],
)
def test_design_system_denied_absent_is_empty(kit):
    assert design_system_denied(kit) == ((), ())


def test_exact_six_digit_hex_hits():
    assert scan_denied("color: #1D58FC;", FONTS, HEXES) == [("hex", "#1D58FC")]


def test_lowercase_text_hits():
    assert scan_denied("fill=#1d58fc", FONTS, HEXES) == [("hex", "#1D58FC")]


def test_eight_digit_alpha_hex_hits():
    assert scan_denied('"#1D58FCFF"', FONTS, HEXES) == [("hex", "#1D58FC")]


def test_seven_digit_run_is_not_a_colour_and_does_not_hit():
    assert scan_denied("#1D58FC0", FONTS, HEXES) == []


def test_nine_digit_run_does_not_hit():
    assert scan_denied("#1D58FCFF0", FONTS, HEXES) == []


def test_font_word_boundary_case_insensitive():
    assert scan_denied("font-family: oldface, sans", FONTS, HEXES) == [("font", "Oldface")]
    assert scan_denied("Oldfaceless", FONTS, HEXES) == []


def test_empty_tokens_ignored():
    assert scan_denied("anything", ("",), ("",)) == []


def test_per_line_wrapper_keeps_message_format_and_new_alpha_semantics():
    errs = _scan_text_for_denied_tokens("a\nfill #1D58FCAA\n", "f.md", list(FONTS), list(HEXES))
    assert errs == [
        "DS-DRIFT-03: f.md:2 contains denied hex '#1D58FC'. "
        "Remove retired hex and use the active [palette] tokens in BRAND.toml."
    ]


_BASE_DS = {
    "archetype": "mission-control",
    "product_override_policy": "suite_gradient_and_logo_only",
    "panel_style": "glass-hairline",
    "background_plates_in_ui": False,
    "require_2d_telemetry": True,
    "forbid_gradient_fill_buttons": True,
    "denied_fonts": [],
    "denied_hexes": [],
}


def test_new_policy_booleans_optional():
    ok, errs = validate_design_system_policy({"design_system": dict(_BASE_DS)})
    assert ok, errs


@pytest.mark.parametrize("key", ["accent_as_fill", "allow_3d_canvas_in_video"])
@pytest.mark.parametrize("val", [True, False])
def test_new_policy_booleans_accept_bool(key, val):
    ok, errs = validate_design_system_policy({"design_system": {**_BASE_DS, key: val}})
    assert ok, errs


@pytest.mark.parametrize("key", ["accent_as_fill", "allow_3d_canvas_in_video"])
@pytest.mark.parametrize("val", ["false", 0, None, ""])
def test_new_policy_booleans_refuse_wrong_type(key, val):
    ok, errs = validate_design_system_policy({"design_system": {**_BASE_DS, key: val}})
    assert not ok
    assert errs == [f"DS-SCHEMA-01: design_system.{key}={val!r} must be a boolean (true/false)."]
