"""Tests for identity consistency and element invariant enforcement (10x Video PRD §2)."""

from __future__ import annotations

from pathlib import Path

from gtm_core.craft_lint import _lint_element_ref, _lint_visual_invariant
from gtm_core.storyboard import get_persisted_identity_anchor, persist_identity_anchors


def test_shots_lint_errors_on_character_shot_without_element_ref():
    """A shot with a person in frame (expression non-empty) and no
    element_ref must produce an ERROR, not a WARN."""
    shot = {
        "expression": "brows up slightly",
        "role": "presenter",
        "visual": "a founder talking directly to camera",
        "duration_s": 4.0,
    }
    errors: list[str] = []
    _lint_element_ref(shot, "shot[1]", errors)
    assert len(errors) == 1
    assert "element_ref" in errors[0]
    assert "person in frame" in errors[0]


def test_shots_lint_passes_broll_without_element_ref():
    """B-roll shots without people do not require element_ref."""
    shot = {
        "role": "broll",
        "visual": "a laptop on a desk",
        "duration_s": 3.0,
    }
    errors: list[str] = []
    _lint_element_ref(shot, "shot[1]", errors)
    assert errors == []


def test_shots_lint_warns_on_mismatched_invariant_across_same_element():
    """Two shots referencing the same element_ref with different
    visual_invariant values must produce a WARN."""
    shots = [
        {"element_ref": "bao-01", "visual_invariant": "dark blue polo, warm light"},
        {"element_ref": "bao-01", "visual_invariant": "grey hoodie, cold light"},
    ]
    warnings: list[str] = []
    _lint_visual_invariant(shots, warnings)
    assert len(warnings) == 1
    assert "visual_invariant" in warnings[0]
    assert "bao-01" in warnings[0]


def test_shots_lint_passes_matching_invariant():
    """Same element_ref with same visual_invariant is clean."""
    shots = [
        {"element_ref": "bao-01", "visual_invariant": "dark blue polo"},
        {"element_ref": "bao-01", "visual_invariant": "dark blue polo"},
    ]
    warnings: list[str] = []
    _lint_visual_invariant(shots, warnings)
    assert warnings == []


def test_different_elements_may_have_different_invariants():
    """Invariant check is per-element, not global."""
    shots = [
        {"element_ref": "bao-01", "visual_invariant": "dark blue polo"},
        {"element_ref": "alex-02", "visual_invariant": "grey hoodie"},
    ]
    warnings: list[str] = []
    _lint_visual_invariant(shots, warnings)
    assert warnings == []


def test_identity_anchor_persistence_and_retrieval(tmp_path: Path):
    """Q6: storyboard identity anchors must be locked to provider job ID upon approval."""
    content_root = tmp_path / "content"
    profile = "test-prof"

    storyboard_data = {
        "approved": True,
        "approved_by": "operator-alice",
        "approved_at": "2026-09-12T10:00:00Z",
        "entries": [
            {
                "n": 1,
                "image_job_id": "job-hf-999",
                "image_path": "content/test-prof/video/run/stills/hero-01.png",
                "identity_anchor": {"kind": "element", "id": "bao-element-uuid"},
            }
        ],
    }

    persisted = persist_identity_anchors(content_root, profile, storyboard_data)
    assert "bao-element-uuid" in persisted
    assert persisted["bao-element-uuid"]["provider_job_id"] == "job-hf-999"
    assert (
        persisted["bao-element-uuid"]["approved_frame"]
        == "content/test-prof/video/run/stills/hero-01.png"
    )

    # Verify retrieval
    retrieved = get_persisted_identity_anchor(content_root, profile, "bao-element-uuid")
    assert retrieved is not None
    assert retrieved["provider_job_id"] == "job-hf-999"

    # Non-existent returns None
    assert get_persisted_identity_anchor(content_root, profile, "non-existent") is None
