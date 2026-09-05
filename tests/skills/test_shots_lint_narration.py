"""The NARRATION lane — a read laid over an already-cut timeline, and now linted as one.

Before 2026-09-05 a shot list could express speech only as a PER-SHOT lip-sync contract. Both
per-shot fields are right about the lane they price: ``spoken`` marks a shot an audio-capable
video model will render, ``vo_seconds`` binds that shot's ``duration_s`` to the mux's
truncate/atempo ceilings. Neither fits a voice-over laid on its own track over a finished cut,
where ``duration_s`` is final-cut length rather than a render request and the gaps between lines
are audio design. Measured on the 2026-09-04 launch film: the real read carried on the shots fired
7 vo-parity errors, and 16 duration errors with ``vo_seconds`` removed — so it was filed under
``production`` and the film's audio script became invisible to every gate.

This module pins the lane in both directions: what it accepts, what it refuses, and that the
per-shot lane is untouched by its existence.
"""

from __future__ import annotations

import pytest

from gtm_core import shots_lint as sl
from gtm_core.shots_lint.schema import schema_errors


def _shot(**overrides) -> dict:
    shot = {
        "id": "s01",
        "duration_s": 5,
        "camera": "static, slow push-in",
        "motion_prompt": "the paper cup tips and rights itself on the table",
        "visual": "a paper cup on a table",
        "role": "broll",
        # Narration shots carry no `spoken`, so the audio-bed rule applies to every one of them.
        "audio_bed": "room tone under the whole beat",
    }
    shot.update(overrides)
    return shot


def _doc(narration: dict | None = None, **overrides) -> dict:
    """A two-shot, 10s cut carrying a clean two-line read."""
    doc = {
        "source_item": "item-1",
        "total_duration_s": 10,
        "style_scaffold": {"look": "editorial monochrome, fine grain", "provider_model": "m"},
        "shots": [_shot(id="s01"), _shot(id="s02")],
        "narration": narration
        if narration is not None
        else {
            "note": "Placed by voice onset; line two leads its picture cut on purpose.",
            "lines": [
                {
                    "line": "The kettle boiled twice before anyone noticed.",
                    "voice_onset_s": 0.45,
                    "len_s": 1.79,
                },
                {
                    "line": "So most mornings, it just sits there.",
                    "voice_onset_s": 4.2,
                    "len_s": 1.71,
                },
            ],
        },
    }
    doc.update(overrides)
    return doc


def _lines(*pairs: tuple[float, float]) -> dict:
    return {"lines": [{"line": f"line {i}", "voice_onset_s": o, "len_s": n}
                      for i, (o, n) in enumerate(pairs, 1)]}  # fmt: skip


# --- the clean path -------------------------------------------------------------------------


def test_a_valid_narration_file_lints_clean():
    """The whole point: the read is now expressible AND checkable, with no findings."""
    errors, warnings = sl.lint_shotlist(_doc())
    assert (errors, warnings) == ([], [])


def test_a_valid_narration_file_conforms_to_the_schema():
    assert schema_errors(_doc()) == []


def test_lines_may_butt_up_against_each_other():
    """Zero gap is a legitimate edit, not an overlap — the check is on audio, not on breathing."""
    errors, _ = sl.lint_shotlist(_doc(_lines((0.0, 2.0), (2.0, 2.0))))
    assert errors == []


def test_a_line_ending_exactly_at_the_cut_is_allowed():
    errors, _ = sl.lint_shotlist(_doc(_lines((8.0, 2.0))))
    assert errors == []


def test_millisecond_rounding_is_not_an_overlap():
    """Onsets come from TTS word timestamps and lengths from a measured file; both are rounded by
    the time they reach this file. The tolerance is that rounding, and nothing wider."""
    errors, _ = sl.lint_shotlist(_doc(_lines((0.0, 2.0), (1.98, 2.0))))
    assert errors == []


# --- what the lane refuses -----------------------------------------------------------------


def test_overlapping_lines_are_refused():
    errors, _ = sl.lint_shotlist(_doc(_lines((0.0, 3.0), (1.5, 2.0))))
    assert len(errors) == 1
    assert "narration.lines[2] starts at 1.5s but lines[1] is still speaking until 3s" in errors[0]
    assert "1.50s of the two reads overlap" in errors[0]


def test_an_audible_backwards_onset_is_still_caught_as_an_overlap():
    """The ordering rule was dropped as a claim about file convention rather than about the film.
    A backwards onset that actually collides is still refused — by the rule that is about sound."""
    errors, _ = sl.lint_shotlist(_doc(_lines((5.0, 3.0), (4.0, 2.0))))
    assert len(errors) == 1
    assert "lines[1] starts at 5s but lines[2] is still speaking until 6s" in errors[0]


def test_a_backwards_onset_that_collides_with_nothing_is_left_alone():
    """The other half of that decision, and the reason overlap is scanned in ONSET order rather
    than file order: [5,6] and [2,3] do not collide, whichever way round they are written. Scanned
    in file order this pair reads as a 4s overlap — a false positive the dropped ordering rule was
    masking, because it fired first and the checks are `elif`-exclusive."""
    assert sl.lint_shotlist(_doc(_lines((5.0, 1.0), (2.0, 1.0)))) == ([], [])


def test_a_read_written_out_of_order_is_linted_as_the_timeline_it_describes():
    """The same file, twice, in two orders: the findings must not depend on the writing order."""
    forward = sl.lint_shotlist(_doc(_lines((0.0, 3.0), (1.5, 2.0))))
    backward = sl.lint_shotlist(_doc(_lines((1.5, 2.0), (0.0, 3.0))))
    assert len(forward[0]) == len(backward[0]) == 1
    assert "1.50s of the two reads overlap" in forward[0][0]
    assert "1.50s of the two reads overlap" in backward[0][0]


def test_an_overlap_is_measured_against_the_longest_line_so_far():
    """A short line nested inside a long one must not reset the running end — the line AFTER it
    still overlaps the long one, and a linter that forgot that would clear it."""
    errors, _ = sl.lint_shotlist(_doc(_lines((0.0, 6.0), (1.0, 0.5), (2.0, 1.0))))
    assert len(errors) == 2
    assert all("lines[1] is still speaking until 6s" in e for e in errors)


def test_a_line_running_past_the_end_of_the_cut_is_refused():
    errors, _ = sl.lint_shotlist(_doc(_lines((9.0, 2.0)), total_duration_s=10))
    assert len(errors) == 1
    assert "narration.lines[1] ends at 11.00s, past total_duration_s=10" in errors[0]
    assert "the film ends mid-sentence" in errors[0]


@pytest.mark.parametrize(
    ("entry", "fragment"),
    [
        ({"line": "", "voice_onset_s": 0.0, "len_s": 1.0}, "has no `line`"),
        ({"line": "a", "voice_onset_s": -1.0, "len_s": 1.0}, "voice_onset_s must be a number >= 0"),
        ({"line": "a", "voice_onset_s": "0", "len_s": 1.0}, "voice_onset_s must be a number >= 0"),
        ({"line": "a", "voice_onset_s": 0.0, "len_s": 0}, "len_s must be a number > 0"),
        ({"line": "a", "voice_onset_s": 0.0}, "len_s must be a number > 0"),
    ],
)
def test_a_malformed_line_is_named_by_the_craft_lint_too(entry: dict, fragment: str):
    """The craft lint runs under ``--no-schema``, so it cannot lean on the schema for typing —
    and a line missing a number would otherwise poison the arithmetic rules silently."""
    errors, _ = sl.lint_shotlist(_doc({"lines": [entry]}))
    assert any(fragment in e for e in errors), errors


def test_a_malformed_line_is_skipped_rather_than_poisoning_the_timeline():
    """It is named once. The lines around it are still checked against each other."""
    errors, _ = sl.lint_shotlist(
        _doc(
            {
                "lines": [
                    {"line": "one", "voice_onset_s": 0.0, "len_s": 3.0},
                    {"line": "two", "voice_onset_s": 1.0},
                    {"line": "three", "voice_onset_s": 1.0, "len_s": 1.0},
                ]
            }
        )  # fmt: skip
    )
    assert len(errors) == 2
    assert "lines[2] len_s must be a number > 0" in errors[0]
    assert "lines[3] starts at 1s but lines[1] is still speaking" in errors[1]


@pytest.mark.parametrize("narration", ["a read", [], {}, {"lines": []}, {"lines": {}}])
def test_a_narration_key_that_is_not_a_track_is_refused(narration):
    """One message for every shape of "the key claims a read and there is none" — a present key
    with nothing behind it must never read as "no narration lane"."""
    errors, _ = sl.lint_shotlist(_doc(narration))
    assert len(errors) == 1
    assert errors[0].startswith("narration must be an object with a non-empty `lines` array")


def test_a_line_is_refused_by_the_schema_when_it_omits_a_required_field():
    doc = _doc({"lines": [{"line": "a", "voice_onset_s": 0.0}]})
    assert schema_errors(doc)


def test_a_misspelt_narration_field_is_still_a_typo():
    """``additionalProperties`` stays closed inside the lane, as it is everywhere else here."""
    doc = _doc({"lines": [{"line": "a", "voice_onset_s": 0.0, "len_s": 1.0, "len_sec": 1.0}]})
    assert schema_errors(doc)


# --- the two lanes are mutually exclusive ---------------------------------------------------


def test_a_file_declaring_both_lanes_is_refused():
    doc = _doc()
    doc["shots"][0]["spoken"] = "The kettle boiled twice before anyone noticed."
    errors, _ = sl.lint_shotlist(doc)
    assert any("narration is declared alongside the per-shot lip-sync lane" in e for e in errors)
    assert any("shot[1].spoken" in e for e in errors)


def test_vo_seconds_alone_also_collides_with_the_lane():
    """``vo_seconds`` is the other half of the per-shot contract and states it just as loudly:
    it binds ``duration_s`` to the mux ceilings, which is exactly what a finished cut is not."""
    doc = _doc()
    doc["shots"][1]["vo_seconds"] = 3.2
    errors, _ = sl.lint_shotlist(doc)
    assert any("shot[2].vo_seconds" in e for e in errors)


def test_the_conflict_names_every_offending_shot_at_once():
    """One message listing all of them — a per-shot message would make an all-shots file
    unreadable, which is the finding-budget lesson."""
    doc = _doc()
    doc["shots"][0]["spoken"] = "one"
    doc["shots"][1]["spoken"] = "two"
    doc["shots"][1]["vo_seconds"] = 2.0
    conflicts = [e for e in sl.lint_shotlist(doc)[0] if e.startswith("narration is declared")]
    assert len(conflicts) == 1
    assert "shot[1].spoken, shot[2].spoken, shot[2].vo_seconds" in conflicts[0]


def test_an_empty_spoken_string_is_not_a_declaration_of_the_other_lane():
    """A shot may legitimately carry ``spoken: ""`` from an earlier draft; the audio-bed rule
    already treats that as silent, and so does this one."""
    doc = _doc()
    doc["shots"][0]["spoken"] = ""
    errors, _ = sl.lint_shotlist(doc)
    assert errors == []


# --- duty cycle: the number duck depth scales inversely with ---------------------------------


def test_a_high_duty_cycle_warns_about_the_default_duck_depth():
    errors, warnings = sl.lint_shotlist(_doc(_lines((0.0, 3.0), (3.5, 4.0))))
    assert errors == []
    assert len(warnings) == 1
    assert "speech duty cycle is 70.0%" in warnings[0]
    assert "duck_music_bed" in warnings[0]


def test_a_low_duty_cycle_is_not_a_finding():
    """A sparse read over a b-roll film is a format, not a defect. Silence has its own gate."""
    _, warnings = sl.lint_shotlist(_doc(_lines((0.0, 1.0), (5.0, 1.0))))
    assert warnings == []


def test_the_duty_threshold_is_a_warning_and_never_an_error():
    """Duck depth is a mixing decision made by ear. This rule hands the operator the number
    before they reach for the defaults; it does not cap how much a film may talk."""
    errors, warnings = sl.lint_shotlist(_doc(_lines((0.0, 9.5))))
    assert errors == []
    assert warnings and "speech duty cycle is 95.0%" in warnings[0]


def test_the_published_threshold_is_the_one_the_rule_enforces():
    """``--rules`` is what callers write to; a hand-copied target is what went stale before."""
    assert sl._active_constants()["narration_duty_warn_pct"] == sl._NARRATION_DUTY_WARN_PCT
    assert sl._active_constants()["narration_field"] == sl._NARRATION_FIELD


# --- the per-shot lane is untouched ----------------------------------------------------------


def test_the_per_shot_lip_sync_lane_is_unchanged_by_the_new_key():
    """The regression this feature must not cause. A file with no ``narration`` sees exactly the
    rules it saw before, ``vo_seconds`` parity included."""
    doc = _doc(narration=None)
    doc.pop("narration")
    doc["vo_source"] = "operator-recorded WAV"
    doc["shots"][0].update(
        spoken="The kettle boiled twice before anyone noticed.",
        role="presenter",
        motion_prompt="he speaks directly to camera, articulating each word",
        vo_seconds=9.4,
    )
    errors, _ = sl.lint_shotlist(doc)
    assert any("against a measured 9.40s VO" in e for e in errors), errors


def test_a_file_with_no_narration_key_runs_no_narration_rule():
    doc = _doc(narration=None)
    doc.pop("narration")
    assert sl.lint_shotlist(doc) == ([], [])
