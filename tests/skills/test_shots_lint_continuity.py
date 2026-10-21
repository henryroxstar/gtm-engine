"""C4 — the cross-shot reference rule fires on a pointer and stays quiet on a description.

The defect (PRD 2026-09-06 §2.4, finding F1): a prompt field that says "the same jacket as shot 1"
reads as an enforced constraint and is inert, because each shot is rendered on its own. Twelve such
clauses shipped across 188 shots and none was noticed.

Two things about these fixtures, stated rather than implied:

* **They are shape-derived, not the corpus rows.** The scan lives outside this repo, on the
  operator's machine, and its rows carry third-party identities that may not cross into this tree
  (§R9). Each case below reproduces one HIT SHAPE the finding records — a numbered shot, a
  positional shot, "as before", a dangling reference ordinal — on invented wardrobe and set nouns.
* **The false-negative half carries the weight.** The first attempt at this rule keyed on the words
  "same" and "still" and returned a 36% false-positive rate; "one open hand resting still at chest
  height" is the control that broke it, and it is named here verbatim.
"""

from __future__ import annotations

import pytest

from gtm_core import shots_lint as sl


def _warnings(field: str, text: str, **shot_extra) -> list[str]:
    shot = {
        "duration_s": 5,
        "camera": "static medium",
        "visual": "a plain studio wall",
        "motion_prompt": "the presenter turns one page of a notebook",
        "role": "broll",
        **shot_extra,
    }
    shot[field] = text
    warnings: list[str] = []
    sl._lint_cross_shot_reference(shot, "shot[2]", warnings)
    return warnings


# ── C4-T1 · the twelve hit shapes all fire ────────────────────────────────────────────


#: One row per hit shape the F1 scan records, fictionalised. ``field`` is where the clause sat.
HIT_SHAPES: tuple[tuple[str, str, str], ...] = (
    ("numbered shot", "wardrobe", "the same linen jacket as shot 1"),
    ("numbered frame", "visual", "the corner desk from frame 3, lit the same way"),
    ("numbered beat", "stability", "hold the wall colour used in beat 2"),
    ("numbered scene", "visual", "back in the workshop of scene 4"),
    ("numbered clip", "motion_prompt", "picks up the action where clip 5 ended"),
    ("previous shot", "wardrobe", "the outfit stays as it was in the previous shot"),
    ("prior frame", "lighting", "the key light matches the prior frame"),
    ("last shot", "expression", "the same half-smile as the last shot"),
    ("opening shot", "visual", "the same doorway as the opening shot"),
    ("as before", "stability", "the mug on the desk stays as before"),
    ("same as above", "wardrobe", "collar and cuffs same as above"),
    ("earlier in the film", "visual", "the whiteboard seen earlier in the film"),
)


@pytest.mark.parametrize(("shape", "field", "text"), HIT_SHAPES, ids=[s for s, _, _ in HIT_SHAPES])
def test_every_recorded_hit_shape_warns(shape: str, field: str, text: str):
    """Each shape the scan found is a pointer at something the renderer never receives."""
    warnings = _warnings(field, text)
    assert warnings, f"{shape}: {text!r} points at another shot and did not warn"
    assert "another shot" in warnings[0], warnings[0]


# ── C4-T2 · the negative controls stay clean ──────────────────────────────────────────


#: Named individually, because a linter that flags these teaches authors to route around it.
NEGATIVE_CONTROLS: tuple[tuple[str, str, str], ...] = (
    # The control that broke the first attempt: "still" is an adverb here, not a pointer.
    ("still as an adverb", "motion_prompt", "one open hand resting still at chest height"),
    ("same within one shot", "visual", "two chairs of the same pale oak, side by side"),
    ("a plain description", "visual", "a narrow galley kitchen at first light"),
    (
        "locked-off camera term",
        "motion_prompt",
        "the performer holds a single position, camera still",
    ),
    ("ordinal about the subject", "visual", "the second shelf holds a row of ceramic jars"),
)


@pytest.mark.parametrize(
    ("shape", "field", "text"),
    NEGATIVE_CONTROLS,
    ids=[s for s, _, _ in NEGATIVE_CONTROLS],
)
def test_recorded_negative_controls_stay_clean(shape: str, field: str, text: str):
    """A description of what is in THIS frame is not a pointer at another one."""
    assert _warnings(field, text) == [], f"{shape}: {text!r} is a description, not a reference"


# ── C4-T3..T5 · what makes a reference resolvable ─────────────────────────────────────


def test_a_reference_to_an_input_file_stays_legal():
    """C4-T3 — a named file is a reference the render actually receives."""
    assert _warnings("wardrobe", "the outfit stays exactly as in refs/ref-b07.jpg") == []


def test_an_ordinal_reference_is_clean_when_the_image_is_attached():
    """C4-T4 — with the stills attached, "the second reference image" addresses something real."""
    warnings = _warnings(
        "visual",
        "the subject from the first reference image, in the office from the second reference image",
        reference_images=["refs/hero.png", "refs/office.png"],
    )
    assert warnings == [], warnings


def test_an_ordinal_reference_warns_when_nothing_is_attached():
    """C4-T5 — the silent case: the clause reads as enforcement and points at nothing.

    This row exists only because C1 and C4 land together. A naive implementation gets it wrong in
    the silent direction — it passes a shot whose reference is a promise nobody kept.
    """
    warnings = _warnings("visual", "the subject from the second reference image")
    assert warnings, "an unattached reference ordinal must warn"
    assert "the shot attaches none" in warnings[0], warnings[0]


def test_an_ordinal_past_the_end_of_the_list_warns():
    """Attaching two stills does not make "the third reference image" resolvable."""
    warnings = _warnings(
        "visual",
        "the jacket from the third reference image",
        reference_images=["refs/hero.png", "refs/office.png"],
    )
    assert warnings, "an ordinal past the end of the list must warn"
    assert "only 2 attached" in warnings[0], warnings[0]


# ── C4-T6 · severity, and what the rule does not scan ─────────────────────────────────


def test_it_is_a_warning_and_never_an_error():
    """C4-T6 — WARN standing is what makes one corpus acceptable evidence (PRD §3.5).

    Asserted on which list the message lands in, because ``shots_lint`` has no severity field: a
    later well-meaning promotion to ERROR trips this test rather than the operator.
    """
    doc = {
        "source_item": "item-1",
        "total_duration_s": 5,
        "style_scaffold": {"look": "warm daylight", "provider_model": "test-model"},
        "shots": [
            {
                "duration_s": 5,
                "camera": "static medium",
                "visual": "the same doorway as shot 1",
                "motion_prompt": "the presenter steps through and stops",
                "role": "broll",
            }
        ],
    }
    errors, warnings = sl.lint_shotlist(doc)
    assert any("refers to another shot" in w for w in warnings), warnings
    assert not any("refers to another shot" in e for e in errors), errors


def test_spoken_dialogue_is_not_scanned():
    """A line the presenter says may legitimately say "same as before" — it is read, not rendered."""
    assert _warnings("visual", "a plain studio wall", spoken="It is the same as before.") == []


def test_the_rule_applies_on_both_capture_modes():
    """A live-action list has the same defect: a camera operator is not the renderer either."""
    for capture_mode in ("rendered", "live_action"):
        doc = {
            "source_item": "item-1",
            "total_duration_s": 5,
            "capture_mode": capture_mode,
            "style_scaffold": {"look": "warm daylight", "provider_model": "test-model"},
            "shots": [
                {
                    "duration_s": 5,
                    "camera": "static medium",
                    "visual": "the same doorway as shot 1",
                    "motion_prompt": "the presenter steps through and stops",
                    "role": "broll",
                }
            ],
        }
        _, warnings = sl.lint_shotlist(doc)
        assert any("refers to another shot" in w for w in warnings), (capture_mode, warnings)


def test_the_rule_publishes_itself_to_the_rule_registry():
    """A rule invisible to ``--rules`` is invisible to video_preflight's constraint report."""
    names = [rule["name"] for rule in sl.active_rules()]
    assert "_lint_cross_shot_reference" in names, names
    summary = next(
        r["summary"] for r in sl.active_rules() if r["name"] == "_lint_cross_shot_reference"
    )
    assert summary, "the rule needs a docstring — its first line is what `--rules` publishes"
