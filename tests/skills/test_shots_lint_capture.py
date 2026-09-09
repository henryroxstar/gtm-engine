"""C3 — the capture contract (`gtm_core.shots_lint.capture`).

PRD test ids C3-T3 (an omitted `capture_mode` gets the strict default), C3-T4 (a live shot never
reaches an engine resolver), C3-T5/T6 (capture_mode cannot suppress the disclosure duty), C3-T8
(the fields are refused on a rendered list and accepted on a live one), C3-T9 (the lock-off WARN
and its five named negative controls) and C3-T10 (the markdown twin is derived).

The composite regex is the risky part — a wordlist of effect nouns is what produces a false
positive on "the effect of the change on revenue" — so each negative control is its own test with
its own name, the way C4's were.
"""

from __future__ import annotations

import pytest

from gtm_core.shots_lint import lint_shotlist
from gtm_core.shots_lint.capture import CAPTURE_FIELDS, render_shotlist_markdown

_CAPTURE_VALUES = {
    "lockoff": True,
    "dead_zone": "lower third, captions",
    "coverage": {"takes": 3, "plate": True},
    "frame_margin": "10%",
}


def _doc(*, live: bool, shots: list[dict] | None = None) -> dict:
    doc = {
        "source_item": "item-1",
        "total_duration_s": 4.0,
        "style_scaffold": {"look": "warm workshop daylight", "provider_model": "n/a"},
        "shots": shots
        or [
            {
                "camera": "locked-off wide, tripod",
                "visual": "the bench, morning light",
                "motion_prompt": "she sets the dial down and looks up",
                "duration_s": 4.0,
                "role": "broll",
                "audio_bed": "room tone",
            }
        ],
    }
    if live:
        doc["capture_mode"] = "live_action"
    return doc


def _findings(*, live: bool, **shot_overrides):
    doc = _doc(live=live)
    doc["shots"][0].update(shot_overrides)
    return lint_shotlist(doc)


# ── C3-T8 / C3-T3: the fields belong to one mode ──────────────────────────────────────────────


@pytest.mark.parametrize("field", CAPTURE_FIELDS)
def test_each_capture_field_is_refused_on_a_rendered_list(field):
    """A render has no camera to lock, so this is direction nobody can carry out."""
    errors, _ = _findings(live=False, **{field: _CAPTURE_VALUES[field]})
    assert [e for e in errors if "capture field" in e], f"{field} was accepted on a rendered list"


def test_every_capture_field_together_is_accepted_on_a_live_list():
    """Positive control for all four at once."""
    errors, _ = _findings(live=True, **_CAPTURE_VALUES)
    assert not [e for e in errors if "capture field" in e]


def test_an_omitted_capture_mode_gets_the_rendered_rules_and_refuses_the_fields():
    """C3-T3. The strict default: a shot list that forgot to say is not assumed to be a shoot."""
    doc = _doc(live=False)
    doc["shots"][0]["lockoff"] = True
    assert "capture_mode" not in doc
    errors, _ = lint_shotlist(doc)
    assert [e for e in errors if "capture field" in e], (
        "an unstated capture_mode opened the live-action exemptions"
    )


# ── C3-T4: a live shot never costs an engine resolution ───────────────────────────────────────


def test_a_live_action_list_never_reaches_the_engine_resolver(monkeypatch):
    """C3-T4. There is nothing to resolve: the operator is the camera and the presenter."""
    import gtm_core.render_engines as render_engines

    def _boom(*a, **k):  # pragma: no cover — the point is that it never runs
        raise AssertionError("a live-action list resolved a render engine")

    monkeypatch.setattr(render_engines, "resolve_engine", _boom)
    monkeypatch.setattr(render_engines, "engine_for_shot_role", _boom)

    doc = _doc(
        live=True,
        shots=[
            {
                "camera": "static medium",
                "visual": "the bench",
                "motion_prompt": "she speaks directly to camera, articulating each word",
                "duration_s": 4.0,
                "role": "presenter",
                "spoken": "This took four months.",
            }
        ],
    )
    lint_shotlist(doc, voice_grade="instant", voice_id="")  # a kit that would fail a rendered list


# ── C3-T5 / C3-T6: capture_mode cannot suppress the disclosure duty ───────────────────────────


def test_capture_mode_cannot_suppress_the_article_50_disclosure_gate():
    """C3-T5. The duty attaches to what was SYNTHESISED, never to what a shot list claims it is.

    A `capture_mode` that could switch the gate off would make the Art. 50 obligation a field the
    same document sets — which is not a gate, it is a preference.
    """
    from agent.publish import validate_disclosure

    line = "Presenter rendered with a consented digital likeness."
    for mode in ("live_action", "rendered", "", "anything-at-all"):
        reason = validate_disclosure(
            f"A post with no disclosure line. capture_mode={mode}",
            ("soul",),
            (line,),
        )
        assert reason, f"capture_mode={mode!r} suppressed the disclosure refusal"


def test_a_live_piece_with_no_identity_marker_needs_no_disclosure_line():
    """C3-T6, the positive control: real footage carries no Art. 50 duty at all."""
    from agent.publish import validate_disclosure

    assert (
        validate_disclosure(
            "A post about a thing we actually filmed.",
            (),
            ("Presenter rendered with a consented digital likeness.",),
        )
        is None
    )


def test_a_live_action_list_still_refuses_a_synthetic_disclosure_key():
    """The existing rule, re-asserted because C3 touches this file: declaring a synthetic
    disclosure on footage of a real person is a FALSE provenance claim, not a safe default."""
    doc = _doc(live=True)
    doc["synthetic_disclosure"] = "Presenter rendered with a consented digital likeness."
    errors, _ = lint_shotlist(doc)
    assert [e for e in errors if "live_action" in e and "synthetic_disclosure" in e]


# ── C3-T9: the lock-off WARN, and its negative controls ───────────────────────────────────────


@pytest.mark.parametrize(
    "visual",
    [
        "the monitor, screen comped in later",
        "the desk with the product keyed over the placeholder",
        "a green screen behind the presenter",
        "the empty bench as a clean plate",
        "the label replaced with the new artwork",
        "the chart overlaid onto the wall",
    ],
)
def test_a_composite_shot_without_lockoff_warns(visual):
    """Two handheld pieces never line up, and stabilisation makes it worse, not better."""
    _, warnings = _findings(live=True, visual=visual)
    assert [w for w in warnings if "lockoff" in w], f"{visual!r} was not read as a composite"


def test_a_composite_shot_that_is_locked_off_is_clean():
    """Negative control 1 — the rule asks for the tripod, and takes yes for an answer."""
    _, warnings = _findings(live=True, visual="the monitor, screen comped in later", lockoff=True)
    assert not [w for w in warnings if "does not set `lockoff`" in w]


def test_a_plain_talking_shot_is_clean():
    """Negative control 2 — nothing is being joined, so nothing needs locking."""
    _, warnings = _findings(live=True, visual="she sits at the bench and talks to camera")
    assert not [w for w in warnings if "lockoff" in w]


def test_the_word_effect_in_a_spoken_line_is_not_a_composite():
    """Negative control 3 — the regression a wordlist of effect nouns would have produced.

    "the effect of the change on revenue" is prose about a subject, said aloud. It is not a
    direction to a camera operator, and `spoken` is not scanned at all.
    """
    _, warnings = _findings(
        live=True,
        visual="she sits at the bench",
        spoken="The effect of the change on revenue was immediate.",
        speech_cue_ok=True,
    )
    assert not [w for w in warnings if "lockoff" in w]


def test_the_word_effect_describing_a_look_is_not_a_composite():
    """Negative control 4 — an effect that is a QUALITY of one frame joins nothing."""
    _, warnings = _findings(live=True, visual="a soft bokeh effect through the window")
    assert not [w for w in warnings if "lockoff" in w]


def test_the_same_composite_wording_on_a_rendered_list_is_clean():
    """Negative control 5 — the rule is live-only. A render has no plate to lock."""
    _, warnings = _findings(live=False, visual="the monitor, screen comped in later")
    assert not [w for w in warnings if "lockoff" in w]


def test_the_lockoff_findings_are_warnings_and_never_errors():
    """Severity is list membership. These shots still shoot; they shoot worse."""
    errors, warnings = _findings(live=True, visual="the monitor, screen comped in later")
    assert [w for w in warnings if "lockoff" in w]
    assert not [e for e in errors if "lockoff" in e]


def test_a_list_with_composites_and_no_locked_off_shot_at_all_warns_once_more():
    """A composite needs two pieces sharing a frame; if every one moves, nothing aligns them."""
    doc = _doc(
        live=True,
        shots=[
            {
                "camera": "handheld",
                "visual": "the monitor, screen comped in",
                "duration_s": 4.0,
                "role": "broll",
                "motion_prompt": "the cursor moves",
                "audio_bed": "room tone",
            },
            {
                "camera": "handheld",
                "visual": "the bench",
                "duration_s": 4.0,
                "role": "broll",
                "motion_prompt": "a hand reaches in",
                "audio_bed": "room tone",
            },
        ],
    )
    _, warnings = lint_shotlist(doc)
    assert [w for w in warnings if "NO shot in this list is locked off" in w]


def test_one_locked_off_shot_satisfies_the_list_level_rule():
    """Positive control: locking the plate is enough — not every shot has to be on a tripod."""
    doc = _doc(
        live=True,
        shots=[
            {
                "camera": "tripod",
                "visual": "the monitor, screen comped in",
                "duration_s": 4.0,
                "role": "broll",
                "motion_prompt": "the cursor moves",
                "audio_bed": "room tone",
                "lockoff": True,
            },
            {
                "camera": "handheld",
                "visual": "the bench",
                "duration_s": 4.0,
                "role": "broll",
                "motion_prompt": "a hand reaches in",
                "audio_bed": "room tone",
            },
        ],
    )
    _, warnings = lint_shotlist(doc)
    assert not [w for w in warnings if "NO shot in this list is locked off" in w]


# ── C3-T10: the twin is derived ───────────────────────────────────────────────────────────────


def test_the_markdown_twin_is_byte_identical_on_a_second_render():
    """Derived means derived: a twin that drifts between renders is a second source of truth."""
    doc = _doc(live=True)
    doc["shots"][0].update(_CAPTURE_VALUES)
    assert render_shotlist_markdown(doc) == render_shotlist_markdown(doc)


def test_the_twin_carries_the_capture_block_on_a_live_list():
    doc = _doc(live=True)
    doc["shots"][0].update(_CAPTURE_VALUES)
    text = render_shotlist_markdown(doc)
    for expected in (
        "Lock off",
        "Dead zone",
        "Coverage",
        "Frame margin",
        "3 takes",
        "plus a clean plate",
        "10%",
    ):
        assert expected in text, f"the shoot twin dropped {expected!r}"


def test_the_twin_omits_the_capture_block_on_a_rendered_list():
    """Still useful for any list — but a render has no lock-off to report."""
    text = render_shotlist_markdown(_doc(live=False))
    assert "Lock off" not in text and "Frame margin" not in text
    assert "Set up." in text and "Camera." in text, "the twin is useful on a rendered list too"


def test_editing_the_twin_changes_nothing_the_linter_reads(tmp_path):
    """The markdown is a projection. Nothing reads it back, and a hand edit is lost on re-render."""
    doc = _doc(live=True)
    doc["shots"][0].update(_CAPTURE_VALUES)
    before = lint_shotlist(doc)
    tampered = render_shotlist_markdown(doc).replace("Lock off", "IGNORE THE TRIPOD")
    assert "IGNORE" in tampered
    assert lint_shotlist(doc) == before, "the linter's verdict depended on the derived twin"
