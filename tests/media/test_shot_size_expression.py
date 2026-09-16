"""Tests for shot size ↔ expression magnitude cross-check (10x Video PRD §4)."""

from __future__ import annotations

from gtm_core.craft_lint import _lint_shot_size_magnitude


def test_extreme_closeup_with_multi_region_expression_warns():
    """An extreme close-up with 3+ facial regions moving is a magnitude
    mismatch — the lexicon says the tighter the frame, the smaller the movement."""
    shot = {
        "shot_size": "extreme_closeup",
        "expression": "brows up, mouth open, eyes wide, jaw dropping",
    }
    warnings: list[str] = []
    _lint_shot_size_magnitude(shot, "shot[1]", warnings)
    assert len(warnings) == 1
    assert "Magnitude mismatch" in warnings[0]
    assert "extreme close-up" in warnings[0].lower() or "extreme_closeup" in warnings[0].lower()


def test_extreme_closeup_with_subtle_expression_passes():
    """An extreme close-up with a single subtle region passes cleanly."""
    shot = {
        "shot_size": "extreme_closeup",
        "expression": "inner brows lifting slightly",
    }
    warnings: list[str] = []
    _lint_shot_size_magnitude(shot, "shot[1]", warnings)
    assert warnings == []


def test_wide_shot_with_face_only_expression_warns():
    """A wide shot relying on face-only direction with no motion_prompt
    will render as invisible — the body carries it at this distance."""
    shot = {
        "shot_size": "wide",
        "expression": "inner brows lifting slightly",
        "motion_prompt": "",
    }
    warnings: list[str] = []
    _lint_shot_size_magnitude(shot, "shot[1]", warnings)
    assert len(warnings) == 1
    assert "body carries it at this distance" in warnings[0]


def test_wide_shot_with_motion_prompt_passes():
    """A wide shot with body motion in motion_prompt passes cleanly."""
    shot = {
        "shot_size": "wide",
        "expression": "inner brows lifting slightly",
        "motion_prompt": "she turns away from the window and walks to the table",
    }
    warnings: list[str] = []
    _lint_shot_size_magnitude(shot, "shot[1]", warnings)
    assert warnings == []
