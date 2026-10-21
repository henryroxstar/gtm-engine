"""gtm_core.shots_lint — a shot's `production.overlay` bubble spec is checked before any spend.

The key was carried on four shots of a real shot list and read by nothing, so a malformed spec
(a compound kind, an empty text, a side that is not a side) would have surfaced at finish time,
after every render dollar — or, worse, not at all. This rule makes the spec a plan-time contract,
and warns when the same shot ALSO asks the render for legible words on the phone screen, because
the whole point of the overlay is that the screen stays blank.
"""

from __future__ import annotations

import json

from gtm_core import shots_lint as sl


def _shot(**overrides) -> dict:
    """A well-formed silent phone shot carrying an overlay. Every unrelated rule is satisfied in
    the baseline so these tests exercise the overlay rule and not an incidental neighbour."""
    shot = {
        "n": 1,
        "duration_s": 4,
        "camera": "static, over the shoulder",
        "motion_prompt": "his thumbs move on the glass and the head tilts toward it",
        "visual": "a dark room, a phone held at a natural angle, its screen blank and evenly lit",
        "role": "broll",
        "audio_bed": "soft keyboard taps under a low note",
        "production": {"overlay": {"kind": "outgoing bubble", "text": "write a post, in my voice"}},
    }
    shot.update(overrides)
    return shot


def _doc(*shots: dict) -> dict:
    return {
        "source_item": "item-x",
        "total_duration_s": sum(s["duration_s"] for s in shots),
        "style_scaffold": {"look": "editorial, fine grain", "provider_model": "wan2_7"},
        "shots": list(shots),
    }


def _overlay_findings(doc: dict) -> tuple[list[str], list[str]]:
    errors, warnings = sl.lint_shotlist(doc)
    return (
        [e for e in errors if "overlay" in e],
        [w for w in warnings if "overlay" in w],
    )


# ── clean ──────────────────────────────────────────────────────────────────────────────────


def test_a_valid_overlay_is_clean():
    assert _overlay_findings(_doc(_shot())) == ([], [])


def test_every_shot_list_spelling_and_short_form_is_accepted():
    for kind in (
        "outgoing bubble",
        "incoming bubble",
        "action chip",
        "outgoing",
        "incoming",
        "chip",
    ):
        errors, _ = _overlay_findings(
            _doc(_shot(production={"overlay": {"kind": kind, "text": "x"}}))
        )
        assert errors == [], kind


def test_optional_placement_keys_are_accepted_when_well_formed():
    overlay = {"kind": "chip", "text": "Open", "side": "left", "y_frac": 0.5, "w_frac": 0.4}
    assert _overlay_findings(_doc(_shot(production={"overlay": overlay}))) == ([], [])


def test_a_shot_without_an_overlay_is_left_alone():
    shot = _shot(production={"note": "no bubble on this beat"})
    assert _overlay_findings(_doc(shot)) == ([], [])


# ── errors ─────────────────────────────────────────────────────────────────────────────────


def test_a_compound_kind_is_refused_and_told_to_be_a_list():
    """The real shot list wrote "action chip, then outgoing bubble" — two overlays in one string.
    A compound kind is unrenderable as written; the refusal names the shape that is."""
    overlay = {"kind": "action chip, then outgoing bubble", "text": "x"}
    errors, _ = _overlay_findings(_doc(_shot(production={"overlay": overlay})))
    assert len(errors) == 1 and ".kind" in errors[0] and "list" in errors[0]


def test_an_empty_text_is_refused():
    errors, _ = _overlay_findings(
        _doc(_shot(production={"overlay": {"kind": "chip", "text": " "}}))
    )
    assert len(errors) == 1 and ".text is missing or empty" in errors[0]


def test_text_past_the_ceiling_is_refused():
    overlay = {"kind": "incoming", "text": "x" * (sl.overlay.OVERLAY_TEXT_MAX_CHARS + 1)}
    errors, _ = _overlay_findings(_doc(_shot(production={"overlay": overlay})))
    assert len(errors) == 1 and "past the 160 ceiling" in errors[0]


def test_a_side_that_is_not_a_side_is_refused():
    overlay = {"kind": "incoming", "text": "x", "side": "up"}
    errors, _ = _overlay_findings(_doc(_shot(production={"overlay": overlay})))
    assert len(errors) == 1 and ".side 'up'" in errors[0]


def test_fractions_outside_the_open_unit_interval_are_refused():
    overlay = {"kind": "incoming", "text": "x", "y_frac": 1.0, "w_frac": "0.5"}
    errors, _ = _overlay_findings(_doc(_shot(production={"overlay": overlay})))
    assert len(errors) == 2
    assert any(".y_frac 1.0" in e for e in errors)
    assert any(".w_frac '0.5'" in e for e in errors)


def test_a_list_of_overlays_is_checked_entry_by_entry_with_its_index():
    overlay = [{"kind": "action chip", "text": "Open"}, {"kind": "reply", "text": "x"}]
    errors, _ = _overlay_findings(_doc(_shot(production={"overlay": overlay})))
    assert len(errors) == 1 and "production.overlay[1].kind" in errors[0]


def test_an_overlay_that_is_not_an_object_is_refused():
    errors, _ = _overlay_findings(_doc(_shot(production={"overlay": "outgoing"})))
    assert len(errors) == 1 and "is not an object" in errors[0]


# ── the screen stays blank ─────────────────────────────────────────────────────────────────


def test_legible_text_on_the_phone_screen_beside_an_overlay_is_a_warning():
    """The overlay exists so the screen can face away from the lens. A shot that asks for the
    words on the screen AND carries them in a bubble has two competing sources of the same text."""
    shot = _shot(visual="a phone with the text on the screen clearly readable as he types")
    errors, warnings = _overlay_findings(_doc(shot))
    assert errors == []
    assert len(warnings) == 1 and "text on the screen" in warnings[0]
    assert "stay blank" in warnings[0]


def test_the_screen_warning_is_silent_without_an_overlay():
    shot = _shot(
        visual="a phone with the text on the screen clearly readable as he types",
        production={},
    )
    _, warnings = _overlay_findings(_doc(shot))
    assert warnings == []


def test_a_blank_screen_description_does_not_trip_the_warning():
    shot = _shot(motion_prompt="the screen stays blank and evenly lit as his thumbs move")
    assert _overlay_findings(_doc(shot)) == ([], [])


# ── the rule publishes itself ──────────────────────────────────────────────────────────────


def test_the_rule_is_listed_by_rules_with_its_constant(capsys):
    assert sl.main(["--rules"]) == 0
    payload = json.loads(capsys.readouterr().out)
    names = {rule["name"] for rule in payload["rules"]}
    assert "_lint_overlay" in names
    assert payload["constants"]["overlay_text_max_chars"] == sl.overlay.OVERLAY_TEXT_MAX_CHARS
