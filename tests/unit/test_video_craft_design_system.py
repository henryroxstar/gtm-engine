"""The tenant's design-system policy, applied to a shot list before spend (PRD 2026-10-19 R14).

Test plan 3.A "Design-system policy (R14)". Every test hands ``design_system.load_kit`` its own
synthetic kit, never the developer's active profile. Fonts and colours are fictional.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gtm_core.video_craft")

from gtm_core import brandkit  # noqa: E402
from gtm_core.screen_ui.mc_video import validate as mc_validate  # noqa: E402
from gtm_core.video_craft import craft_verdict as cv  # noqa: E402
from gtm_core.video_craft import design_system as ds  # noqa: E402
from gtm_core.video_craft import hook  # noqa: E402
from gtm_core.video_craft.words import ONSCREEN_WORDS_WARN  # noqa: E402
from tests.unit import video_craft_fixtures as fx  # noqa: E402

CEILING = ONSCREEN_WORDS_WARN["16:9"]
DENIED_FONT = "Quillstone Display"
DENIED_HEX = "#7A3E9D"


def _kit(**policy) -> dict:
    base = dict(fx.CLEAN_KIT["design_system"])
    base.update(policy)
    return {"design_system": base}


def _findings(monkeypatch, kit, document) -> tuple[list, list[str]]:
    fx.isolate(monkeypatch, kit=kit)
    return hook.craft_findings(document)


def _ds(findings) -> list[tuple[str, str, int | None]]:
    return [(f.rule_id, f.severity, f.cut) for f in findings if f.rule_id == hook.DESIGN_SYSTEM]


def _good_with(index: int, **fields) -> dict:
    document = fx.good(CEILING)
    document["shots"][index].update(fields)
    return document


# ── denied fonts and hexes ────────────────────────────────────────────────────────────────────


def test_a_denied_font_in_a_visual_is_an_error(monkeypatch):
    document = _good_with(1, visual=f"node labels set in {DENIED_FONT}")
    findings, _ = _findings(monkeypatch, _kit(denied_fonts=[DENIED_FONT]), document)
    assert _ds(findings) == [(hook.DESIGN_SYSTEM, "error", 2)]


def test_a_denied_hex_in_screen_ui_copy_is_an_error(monkeypatch):
    document = fx.good(CEILING)
    document["shots"][2]["production"]["screen_ui"]["headline"] = f"tint {DENIED_HEX.lower()}"
    findings, _ = _findings(monkeypatch, _kit(denied_hexes=[DENIED_HEX]), document)
    assert _ds(findings) == [(hook.DESIGN_SYSTEM, "error", 3)]


def test_a_denied_hex_with_alpha_is_still_the_denied_colour(monkeypatch):
    document = _good_with(4, visual=f"wash of {DENIED_HEX}80 over the street")
    findings, _ = _findings(monkeypatch, _kit(denied_hexes=[DENIED_HEX]), document)
    assert _ds(findings) == [(hook.DESIGN_SYSTEM, "error", 5)]


def test_font_and_colour_on_one_cut_is_one_finding(monkeypatch):
    document = _good_with(1, visual=f"{DENIED_FONT} labels on {DENIED_HEX}")
    kit = _kit(denied_fonts=[DENIED_FONT], denied_hexes=[DENIED_HEX])
    findings, _ = _findings(monkeypatch, kit, document)
    assert _ds(findings) == [(hook.DESIGN_SYSTEM, "error", 2)]


def test_an_allowed_font_and_colour_are_not_findings(monkeypatch):
    """Positive control: the scan is not refusing every font or colour."""
    document = _good_with(1, visual="labels set in Fernway Grotesk on #0B1D2A")
    kit = _kit(denied_fonts=[DENIED_FONT], denied_hexes=[DENIED_HEX])
    findings, not_checked = _findings(monkeypatch, kit, document)
    assert findings == [] and not_checked == []


def test_the_scan_is_the_one_the_mission_control_validator_uses(monkeypatch):
    """R14 reuses the DS-DRIFT scan rather than re-implementing it: one function object."""
    assert mc_validate.scan_denied is brandkit.scan_denied
    assert mc_validate.design_system_denied is brandkit.design_system_denied
    calls: list[str] = []
    real = brandkit.scan_denied

    def spy(text, fonts, hexes):
        calls.append(text)
        return real(text, fonts, hexes)

    monkeypatch.setattr(brandkit, "scan_denied", spy)
    document = _good_with(1, visual=f"labels in {DENIED_FONT}")
    findings, _ = _findings(monkeypatch, _kit(denied_fonts=[DENIED_FONT]), document)
    assert calls, "the craft check did not call brandkit.scan_denied"
    assert _ds(findings)


# ── background plates ─────────────────────────────────────────────────────────────────────────

PLATE_VISUAL = "the flow drawn over a background plate of a harbour at night"


@pytest.mark.parametrize("index", [1, 2, 3])  # diagram, kinetic_type, ui_mock in fx.good
def test_a_plate_behind_a_text_carrying_shot_is_an_error_when_plates_are_off(monkeypatch, index):
    document = _good_with(index, visual=PLATE_VISUAL)
    findings, _ = _findings(monkeypatch, _kit(background_plates_in_ui=False), document)
    assert _ds(findings) == [(hook.DESIGN_SYSTEM, "error", index + 1)]


def test_a_plate_behind_a_data_visual_is_an_error_when_plates_are_off(monkeypatch):
    document = fx.good(CEILING)
    document["shots"].append(fx.shot(6, "data_visual", 2.0, visual="bg-harbour-dusk.png"))
    findings, _ = _findings(monkeypatch, _kit(background_plates_in_ui=False), document)
    assert _ds(findings) == [(hook.DESIGN_SYSTEM, "error", 6)]


def test_a_plate_is_accepted_when_plates_are_on(monkeypatch):
    document = _good_with(1, visual=PLATE_VISUAL)
    findings, _ = _findings(monkeypatch, _kit(background_plates_in_ui=True), document)
    assert _ds(findings) == []


def test_a_plate_behind_footage_is_not_a_plate_finding(monkeypatch):
    document = _good_with(4, visual=PLATE_VISUAL)  # photo_broll
    findings, _ = _findings(monkeypatch, _kit(background_plates_in_ui=False), document)
    assert _ds(findings) == []


# ── 3D / diffusion backdrops ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "visual", ["a slow 3D orbit of the device", "a diffusion backdrop of hills", "volumetric fog"]
)
def test_3d_words_are_a_warning_when_3d_is_off(monkeypatch, visual):
    document = _good_with(1, visual=visual)
    findings, _ = _findings(monkeypatch, _kit(allow_3d_canvas_in_video=False), document)
    assert _ds(findings) == [(hook.DESIGN_SYSTEM, "warning", 2)]


def test_3d_words_are_fine_when_3d_is_on(monkeypatch):
    document = _good_with(1, visual="a slow 3D orbit of the device")
    findings, _ = _findings(monkeypatch, _kit(allow_3d_canvas_in_video=True), document)
    assert _ds(findings) == []


# ── no policy: "not set", never a pass ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kit",
    [None, {}, "not a kit", ["design_system"], {"design_system": {}}, {"design_system": "on"}],
)
def test_no_usable_policy_reads_design_system_not_set(monkeypatch, kit):
    findings, not_checked = _findings(monkeypatch, kit, fx.good(CEILING))
    assert _ds(findings) == []
    assert "design system: not set" in not_checked


def test_a_non_dict_kit_is_not_set_at_the_function_too():
    found, unchecked = ds.design_system_findings(fx.good(CEILING)["shots"], "kit", hook.cut_number)
    assert (found, unchecked) == ([], ["design system: not set"])


def test_not_set_reaches_the_verdict_not_checked_list(monkeypatch):
    fx.isolate(monkeypatch, kit=None)
    verdict = cv.craft_verdict(fx.good(CEILING))
    assert "design system: not set" in verdict.not_checked


def test_the_makeable_line_is_never_a_pass_when_the_design_system_is_not_set(monkeypatch):
    fx.isolate(monkeypatch, kit=None)
    verdict = cv.craft_verdict(fx.good(CEILING))
    makeable = next(line for line in verdict.lines if line.question == cv.LINE_MAKEABLE)
    assert makeable.status != "pass"
