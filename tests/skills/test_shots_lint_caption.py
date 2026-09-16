"""Tests for gtm_core.shots_lint.caption (_lint_caption).

Enforces §R18: every rule has its positive control and negative control.
Tests caption_function declaration, describe_share ceiling, and thread pairing.
"""

from __future__ import annotations

from gtm_core.shots_lint import lint_shotlist
from gtm_core.shots_lint.caption import _lint_caption


def _base_shotlist(shots: list[dict]) -> dict:
    return {
        "source_item": "item-1",
        "total_duration_s": sum(s.get("duration_s", 4) for s in shots),
        "deliverable_ratios": ["9:16"],
        "style_scaffold": {"look": "clean grain", "provider_model": "model-1"},
        "shots": shots,
    }


def _shot(i: int, **overrides) -> dict:
    doc = {
        "id": f"s{i:02d}",
        "duration_s": 4,
        "camera": "static shot",
        "motion_prompt": f"action for shot {i} is moving forward smoothly",
        "visual": f"visual scene for shot {i}",
        "role": "broll",
        "audio_bed": "room tone",
    }
    doc.update(overrides)
    return doc


# ── T1 · Positive Control ─────────────────────────────────────────────────────────────


def test_valid_narrative_captions_pass_clean():
    shots = [
        _shot(1, caption_text_override="Sixty miles out. Gale coming.", caption_function="premise"),
        _shot(2, caption_text_override="Checking the vibration alarm.", caption_function="want"),
        _shot(
            3,
            caption_text_override="The gauge said clear.",
            caption_function="setup",
            caption_pairs_with=5,
        ),
        _shot(
            4, caption_text_override="Clean paperwork. Bad feeling.", caption_function="interior"
        ),
        _shot(
            5,
            caption_text_override="The gauge had said clear.",
            caption_function="callback",
            caption_pairs_with=3,
        ),
        _shot(6, caption_text_override="Know when it starts.", caption_function="message"),
    ]
    doc = _base_shotlist(shots)
    errors, warnings = lint_shotlist(doc)
    caption_errors = [e for e in errors if "caption" in e.lower()]
    caption_warnings = [
        w for w in warnings if "caption" in w.lower() or "describe_share" in w.lower()
    ]
    assert caption_errors == []
    assert caption_warnings == []


# ── T2 · Missing and Invalid caption_function ─────────────────────────────────────────


def test_narrative_caption_without_function_errors():
    shots = [
        _shot(1, caption_text_override="A line with no function declared."),
    ]
    errors: list[str] = []
    warnings: list[str] = []
    _lint_caption(shots, errors, warnings)
    assert any("has a narrative caption" in e and "caption_function" in e for e in errors)


def test_invalid_caption_function_errors():
    shots = [
        _shot(1, caption_text_override="Some line.", caption_function="humorous"),
    ]
    errors: list[str] = []
    warnings: list[str] = []
    _lint_caption(shots, errors, warnings)
    assert any("caption_function 'humorous' is not one of" in e for e in errors)


# ── T3 · describe_share Ceiling ───────────────────────────────────────────────────────


def test_describe_share_exceeding_ceiling_warns():
    # 4 non-message narrative shots, 3 are describe -> 75% > 50%
    shots = [
        _shot(1, caption_text_override="She climbs tower.", caption_function="describe"),
        _shot(2, caption_text_override="She plugs in sensor.", caption_function="describe"),
        _shot(3, caption_text_override="She checks numbers.", caption_function="describe"),
        _shot(4, caption_text_override="Clean paperwork.", caption_function="interior"),
        _shot(5, caption_text_override="Know early.", caption_function="message"),
    ]
    errors: list[str] = []
    warnings: list[str] = []
    _lint_caption(shots, errors, warnings)
    assert any("describe_share is 75%" in w and "ceiling" in w for w in warnings)


def test_describe_share_within_ceiling_passes():
    # 4 non-message narrative shots, 1 is describe -> 25% <= 50%
    shots = [
        _shot(1, caption_text_override="She climbs tower.", caption_function="describe"),
        _shot(2, caption_text_override="Checking vibration alarm.", caption_function="want"),
        _shot(3, caption_text_override="From outside, quiet is gone.", caption_function="cost"),
        _shot(4, caption_text_override="Clean paperwork.", caption_function="interior"),
        _shot(5, caption_text_override="Know early.", caption_function="message"),
    ]
    errors: list[str] = []
    warnings: list[str] = []
    _lint_caption(shots, errors, warnings)
    assert not any("describe_share" in w for w in warnings)


# ── T4 · Thread Pairing (setup & callback) ────────────────────────────────────────────


def test_setup_without_callback_errors():
    shots = [
        _shot(1, caption_text_override="The safe choice.", caption_function="setup"),
        _shot(2, caption_text_override="Another shot.", caption_function="interior"),
    ]
    errors: list[str] = []
    warnings: list[str] = []
    _lint_caption(shots, errors, warnings)
    assert any("declares caption_function='setup' but no later shot declares" in e for e in errors)


def test_callback_without_setup_pairing_errors():
    shots = [
        _shot(1, caption_text_override="Another shot.", caption_function="interior"),
        _shot(2, caption_text_override="The safe choice.", caption_function="callback"),
    ]
    errors: list[str] = []
    warnings: list[str] = []
    _lint_caption(shots, errors, warnings)
    assert any("caption_pairs_with is missing" in e for e in errors)


def test_callback_pointing_to_non_setup_shot_errors():
    shots = [
        _shot(1, caption_text_override="Another shot.", caption_function="interior"),
        _shot(
            2,
            caption_text_override="The safe choice.",
            caption_function="callback",
            caption_pairs_with=1,
        ),
    ]
    errors: list[str] = []
    warnings: list[str] = []
    _lint_caption(shots, errors, warnings)
    assert any("does not declare caption_function='setup'" in e for e in errors)


def test_setup_pointing_backwards_errors():
    shots = [
        _shot(1, caption_text_override="First.", caption_function="callback", caption_pairs_with=1),
        _shot(2, caption_text_override="Second.", caption_function="setup", caption_pairs_with=1),
    ]
    errors: list[str] = []
    warnings: list[str] = []
    _lint_caption(shots, errors, warnings)
    assert any("setup must point to a later shot" in e for e in errors)


def test_caption_pairs_with_out_of_range_errors():
    shots = [
        _shot(1, caption_text_override="First.", caption_function="setup", caption_pairs_with=99),
    ]
    errors: list[str] = []
    warnings: list[str] = []
    _lint_caption(shots, errors, warnings)
    assert any("caption_pairs_with=99 names a shot that does not exist" in e for e in errors)


def test_caption_pairs_with_uncaptioned_shot_errors():
    shots = [
        _shot(1),  # uncaptioned
        _shot(
            2, caption_text_override="Callback.", caption_function="callback", caption_pairs_with=1
        ),
    ]
    errors: list[str] = []
    warnings: list[str] = []
    _lint_caption(shots, errors, warnings)
    assert any("names shot[1] which has no caption" in e for e in errors)


# ── T5 · Voiced Shots Subtitle vs Super ────────────────────────────────────────────────


def test_voiced_shot_with_subtitle_labeled_as_super_warns():
    shots = [
        _shot(1, spoken="Good morning team.", caption_function="premise"),
    ]
    errors: list[str] = []
    warnings: list[str] = []
    _lint_caption(shots, errors, warnings)
    assert any("subtitles should not be labeled as narrative captions" in w for w in warnings)


def test_voiced_shot_with_genuine_super_does_not_warn():
    shots = [
        _shot(
            1,
            spoken="Good morning team.",
            caption_text_override="Three years later.",
            caption_function="time",
        ),
    ]
    errors: list[str] = []
    warnings: list[str] = []
    _lint_caption(shots, errors, warnings)
    assert not any("subtitles should not be labeled as narrative captions" in w for w in warnings)
