"""Tests for skin_scaffold lint rule (10x Video PRD §4)."""

from __future__ import annotations

from gtm_core.craft_lint import _lint_skin_scaffold


def test_shots_lint_warns_face_shot_without_skin_scaffold():
    """A shot with expression and no skin-texture counter-phrase in visual/motion_prompt triggers WARN."""
    shot = {
        "expression": "inner brows lifting a fraction",
        "visual": "a woman at a desk, professional setting",
    }
    kit = {}  # no [identity].skin_scaffold
    warnings: list[str] = []
    _lint_skin_scaffold(shot, "shot[1]", warnings, kit=kit)
    assert len(warnings) == 1
    assert "skin_scaffold" in warnings[0]


def test_shots_lint_passes_face_shot_with_skin_scaffold():
    """A shot carrying the brand kit's skin_scaffold phrase is clean."""
    shot = {
        "expression": "inner brows lifting a fraction",
        "visual": "a woman at a desk, natural skin texture, visible pores",
    }
    kit = {"identity": {"skin_scaffold": "natural skin texture, visible pores"}}
    warnings: list[str] = []
    _lint_skin_scaffold(shot, "shot[1]", warnings, kit=kit)
    assert warnings == []


def test_shots_lint_ignores_shot_without_expression():
    """B-roll or screen shots without expression do not trigger skin_scaffold warnings."""
    shot = {
        "visual": "a laptop displaying code",
    }
    warnings: list[str] = []
    _lint_skin_scaffold(shot, "shot[1]", warnings, kit={})
    assert warnings == []
