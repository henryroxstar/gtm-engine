"""Tripwire corpus for ``gtm_core.shots_lint`` — every rule family fires, at its severity, or CI is red.

Read ``tests/tripwire/README.md`` first. ``lint_shotlist`` returns free-text ``(errors, warnings)``
with no rule ids, so a "rule family" here is a message shape: ``FAMILIES`` maps every message the
linter can emit to one id by an anchored prefix regex, and a message that maps to none (or to
two) fails loudly — that is a new or reworded rule, and the corpus must learn it.
"""

from __future__ import annotations

import json
import re

import pytest

from gtm_core import shots_lint as sl
from tests.tripwire import support

CORPUS = support.HERE / "shots_lint"

Family = tuple[str, str]  # (family id, severity)

#: (family id, severity, anchored prefix regex). Severity is which list the message lands in.
FAMILIES: tuple[tuple[str, str, str], ...] = (
    ("not_an_object", "error", r"shot list is not a JSON object$"),
    ("missing_top_level_field", "error", r"missing required top-level field `"),
    ("scaffold_key_missing", "error", r"style_scaffold\.(look|provider_model) is missing or empty$"),
    ("scaffold_not_object", "error", r"style_scaffold is not an object$"),
    ("negation_in_scaffold_look", "error", r"style_scaffold\.look uses negation phrasing"),
    ("capture_mode_unknown", "error", r"capture_mode .* is not one of"),
    ("disclosure_on_live_action", "error", r"synthetic_disclosure is declared on a live_action"),
    ("disclosure_empty", "error", r"synthetic_disclosure is present but empty"),
    ("disclosure_mismatch", "error", r"synthetic_disclosure declares .* which is not the tenant's configured"),
    ("disclosure_no_kit_line", "error", r"synthetic_disclosure declares .* but the brand kit configures no"),
    ("shots_empty", "error", r"shots must be a non-empty array$"),
    ("shot_not_object", "error", r"shot\[\d+\] is not an object$"),
    ("shot_missing_camera", "error", r"shot\[\d+\] missing required field `camera`$"),
    ("shot_missing_visual", "error", r"shot\[\d+\] missing required field `visual`$"),
    ("duration_invalid", "error", r"shot\[\d+\] duration_s must be a number > 0$"),
    ("role_invalid", "error", r"shot\[\d+\] role .* is not one of"),
    ("camera_stacked_moves", "error", r"shot\[\d+\] stacks \d+ camera moves"),
    ("motion_prompt_missing", "error", r"shot\[\d+\] missing required field `motion_prompt`"),
    ("motion_prompt_restates_camera", "error", r"shot\[\d+\] motion_prompt merely restates `camera`"),
    ("gesture_as_diagram", "error", r"shot\[\d+\] motion_prompt contains '"),
    ("vo_measured_silent_tail", "error", r"shot\[\d+\] duration_s=\S+ against a measured .* leaves a"),
    ("vo_measured_atempo", "error", r"shot\[\d+\] duration_s=\S+ against a measured .* needs atempo"),
    ("vo_shorter_than_line", "error", r"shot\[\d+\] duration_s=\S+ is shorter than its own spoken line"),
    ("speech_cue_missing", "error", r"shot\[\d+\] has a spoken line and role="),
    ("in_frame_text", "error", r"shot\[\d+\]\.(visual|motion_prompt) asks the video model to render in-frame text"),
    ("presenter_engine_unresolved", "error", r"shot\[\d+\] is a speaking presenter shot and no engine can serve it"),
    ("audio_bed_missing", "error", r"shot\[\d+\] has no `spoken` line and no `audio_bed`"),
    ("audio_bed_silent_no_reason", "error", r"shot\[\d+\] declares `audio_bed: .*` with no reason"),
    ("voice_grade_not_professional", "error", r"shot\[\d+\] has a spoken line but (voice_grade=|no voice_grade recorded)"),
    ("spoken_without_voice", "error", r"shot\[\d+\] has a `spoken` line but the brand kit records no `identity\.voice_id`"),
    ("negation_in_shot_field", "error", r"shot\[\d+\]\.\w+ uses negation phrasing"),
    ("narration_lane_conflict", "error", r"narration is declared alongside the per-shot lip-sync lane"),
    ("narration_not_a_track", "error", r"narration must be an object with a non-empty `lines` array"),
    ("narration_line_not_an_object", "error", r"narration\.lines\[\d+\] is not an object$"),
    ("narration_line_no_text", "error", r"narration\.lines\[\d+\] has no `line`"),
    ("narration_onset_invalid", "error", r"narration\.lines\[\d+\] voice_onset_s must be a number"),
    ("narration_len_invalid", "error", r"narration\.lines\[\d+\] len_s must be a number"),
    ("narration_overlap", "error", r"narration\.lines\[\d+\] starts at \S+ but lines\["),
    ("narration_past_end", "error", r"narration\.lines\[\d+\] ends at "),
    ("presenter_share_over_ceiling", "error", r"\d+ of \d+ shots are role=presenter"),
    ("look_orientation_mismatch", "error", r"identity_bindings\.\S+ source is \d+x\d+"),
    ("identity_bindings_not_an_object", "error", r"identity_bindings must be an object mapping"),
    ("identity_binding_not_an_object", "error", r"identity_bindings\.\S+ is not a binding object"),
    ("identity_bindings_notes_malformed", "error", r"identity_bindings\.notes is the reserved annotation key"),
    ("identity_binding_no_handle", "warn", r"identity_bindings\.\S+ names no identity handle"),
    ("duration_over_ceiling", "warn", r"shot\[\d+\] duration_s=\S+ exceeds every confirmed provider ceiling"),
    ("camera_unrecognised", "warn", r"shot\[\d+\] camera .* matches no known move or framing term"),
    ("motion_prompt_thin", "warn", r"shot\[\d+\] motion_prompt .* is \d+ words — too thin"),
    ("vo_dead_air", "warn", r"shot\[\d+\] duration_s=\S+ runs \S+s past its spoken line"),
    ("duration_fractional", "warn", r"shot\[\d+\] duration_s=\S+ is fractional"),
    ("duration_below_minimum", "warn", r"shot\[\d+\] duration_s=\S+ is below \d+s"),
    ("all_presenter_small_list", "warn", r"all \d+ shots are role=presenter"),
    ("no_screen_shot", "warn", r"no shot has role=screen"),
    ("narration_duty_cycle", "warn", r"narration speech duty cycle is"),
    ("total_duration_drift", "warn", r"total_duration_s="),
)  # fmt: skip

INVENTORY: frozenset[Family] = frozenset((fid, sev) for fid, sev, _ in FAMILIES)

#: The `_lint_*` helpers ``lint_shotlist`` calls directly — ``active_rules()`` derives this by AST
#: and the `--rules` CLI golden pins the same list with its docstrings.
ACTIVE_RULES = (
    "_lint_audio_bed",
    "_lint_camera",
    "_lint_identity_bindings",
    "_lint_look_ratio",
    "_lint_motion_prompt",
    "_lint_narration_duty_cycle",
    "_lint_narration_lane_exclusive",
    "_lint_narration_timeline",
    "_lint_negation",
    "_lint_no_in_frame_text",
    "_lint_presenter_engine",
    "_lint_shot_mix",
    "_lint_speech_cue",
    "_lint_spoken_has_a_voice",
    "_lint_synthetic_disclosure",
    "_lint_vo_duration_parity",
    "_lint_voice_grade",
)

BAD_ERRORS = [
    "scaffold_key_missing",
    "negation_in_scaffold_look",
    "disclosure_empty",
    "duration_invalid",
    "role_invalid",
    "camera_stacked_moves",
    "motion_prompt_restates_camera",
    "in_frame_text",
    "audio_bed_silent_no_reason",
    "negation_in_shot_field",
    "speech_cue_missing",
    "presenter_engine_unresolved",
    "voice_grade_not_professional",
    "spoken_without_voice",
    "gesture_as_diagram",
    "vo_shorter_than_line",
    "presenter_engine_unresolved",
    "voice_grade_not_professional",
    "spoken_without_voice",
    "shot_missing_visual",
    "voice_grade_not_professional",
    "spoken_without_voice",
    "shot_missing_camera",
    "vo_measured_silent_tail",
    "presenter_engine_unresolved",
    "voice_grade_not_professional",
    "spoken_without_voice",
    "vo_measured_atempo",
    "presenter_engine_unresolved",
    "voice_grade_not_professional",
    "spoken_without_voice",
    "motion_prompt_missing",
    "audio_bed_missing",
    "presenter_share_over_ceiling",
    "look_orientation_mismatch",
]
BAD_WARNINGS = [
    "duration_over_ceiling",
    "camera_unrecognised",
    "motion_prompt_thin",
    "vo_dead_air",
    "duration_below_minimum",
    "duration_fractional",
    "no_screen_shot",
    "total_duration_drift",
]

#: (run id, fixture, kwargs, expected error families in order, expected warning families in order)
RUNS: list[tuple[str, str, dict, list[str], list[str]]] = [
    ("clean", "clean.shots.json", {"voice_grade": "professional", "voice_id": "voice-1"}, [], []),
    # The kit line reaches the linter in three states. Supplied and matching is the only one
    # that clears; a declaration that is not the line (the 2026-09-03 "element" defect) and a
    # tenant with no configured line both fail on the SAME fixture that passes above.
    ("disclosure-match", "clean.shots.json",
     {"voice_grade": "professional", "voice_id": "voice-1", "disclosure_line": "Presenter rendered with a consented digital likeness."}, [], []),
    ("disclosure-mismatch", "clean.shots.json",
     {"voice_grade": "professional", "voice_id": "voice-1", "disclosure_line": "Made with AI."},
     ["disclosure_mismatch"], []),
    ("disclosure-no-kit-line", "clean.shots.json",
     {"voice_grade": "professional", "voice_id": "voice-1", "disclosure_line": ""},
     ["disclosure_no_kit_line"], []),
    ("bad", "shots-bad.shots.json", {"voice_grade": "instant", "voice_id": ""}, BAD_ERRORS, BAD_WARNINGS),
    ("two-presenters", "two-presenters.shots.json", {}, [], ["all_presenter_small_list"]),
    # live_action: the presenter-engine and both voice rules are exempt even with a bad kit.
    ("live-action", "live-action.shots.json", {"voice_grade": "instant", "voice_id": ""},
     ["disclosure_on_live_action", "shot_not_object", "identity_bindings_not_an_object"], []),
    ("broken-top", "broken-top.shots.json", {},
     ["missing_top_level_field", "missing_top_level_field", "scaffold_not_object",
      "capture_mode_unknown", "disclosure_empty", "narration_not_a_track", "shots_empty"], []),
    # The narration lane: a read laid over an already-cut timeline. Clean first — the shape this
    # lane exists to make expressible must lint silently, or the gap simply moved.
    ("narration-clean", "narration-clean.shots.json", {"voice_id": "", "voice_grade": ""}, [], []),
    # Then every way a TRACK goes wrong, on one file: both lanes declared at once, a malformed
    # entry of each kind, two lines talking over each other, a line running past the cut, and a
    # duty cycle past the depth the default duck was tuned for.
    ("narration-bad", "narration-bad.shots.json", {},
     ["narration_lane_conflict", "narration_line_not_an_object", "narration_line_no_text",
      "narration_onset_invalid", "narration_len_invalid", "narration_overlap",
      "narration_past_end"],
     ["narration_duty_cycle", "vo_dead_air"]),
    ("not-an-object", "not-an-object.shots.json", {}, ["not_an_object"], []),
    # The three per-binding shapes: a bare id string (the 2026-09-03 element bug), a binding
    # that identifies nothing, and a note filed where a binding belongs.
    ("identity-bindings", "identity-bindings-bad.shots.json", {},
     ["identity_binding_not_an_object", "identity_bindings_notes_malformed"],
     ["identity_binding_no_handle"]),
]  # fmt: skip


def classify(message: str, severity: str) -> str:
    hits = [fid for fid, sev, rx in FAMILIES if sev == severity and re.match(rx, message)]
    assert len(hits) == 1, f"{severity} message maps to {hits or 'no family'}: {message!r}"
    return hits[0]


def _run(fixture: str, kwargs: dict) -> tuple[list[str], list[str]]:
    doc = json.loads((CORPUS / fixture).read_text(encoding="utf-8"))
    errors, warnings = sl.lint_shotlist(doc, **kwargs)
    return [classify(m, "error") for m in errors], [classify(m, "warn") for m in warnings]


@pytest.mark.parametrize(
    ("fixture", "kwargs", "errors", "warnings"), [r[1:] for r in RUNS], ids=[r[0] for r in RUNS]
)
def test_fixture_fires_exactly(
    fixture: str, kwargs: dict, errors: list[str], warnings: list[str]
) -> None:
    assert _run(fixture, kwargs) == (errors, warnings)


def test_clean_fixture_fires_nothing() -> None:
    assert _run("clean.shots.json", {"voice_grade": "professional", "voice_id": "voice-1"}) == (
        [],
        [],
    )


def test_every_rule_family_is_tripped_by_the_corpus() -> None:
    """Set EQUALITY: a family that stops firing is named, and a new family must join the corpus."""
    fired: set[Family] = set()
    for _, fixture, kwargs, _, _ in RUNS:
        errors, warnings = _run(fixture, kwargs)
        fired.update((fid, "error") for fid in errors)
        fired.update((fid, "warn") for fid in warnings)
    assert fired == INVENTORY


def test_rule_registry_matches_the_inventory() -> None:
    """``active_rules()`` is derived from ``lint_shotlist``'s own source by AST — after a split
    it must still see every helper the entry point calls, and ``_active_constants()`` must still
    publish the same knobs."""
    assert tuple(r["name"] for r in sl.active_rules()) == ACTIVE_RULES
    assert all(r["summary"] != "(no docstring)" for r in sl.active_rules())
    assert sorted(sl._active_constants()) == [
        "MAX_PRESENTER_SHARE",
        "audio_bed_field",
        "max_confirmed_duration_s",
        "narration_duty_warn_pct",
        "narration_field",
        "presenter_quota_min_shots",
        "required_shot_role",
        "valid_roles",
        "vo_source_field",
        "vo_words_per_sec",
    ]


def test_help_golden() -> None:
    support.check_help("shots_lint")


@pytest.mark.parametrize("case", support.CASES["shots_lint"], ids=lambda c: c.name)
def test_cli_golden(case: support.CliCase, tmp_path) -> None:
    support.check_case(case, tmp_path)
