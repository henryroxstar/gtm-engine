"""Tests for gtm_core.video_lint.pixel_gate (Q4)."""

from __future__ import annotations

from gtm_core.video_lint.pixel_gate import inspect_boundary_frames


def test_pixel_gate_passes_congruent():
    shots = [
        {"duration_s": 3.0, "role": "presenter", "expression": "brows lifted with gentle smile"},
        {"duration_s": 4.0, "role": "broll", "expression": "neutral"},
    ]
    frames = [
        {"frame_time_s": 1.5, "au_detected": ["AU12"]},
        {"frame_time_s": 4.5, "au_detected": ["AU4"]},  # broll, ignored
    ]
    verdict = inspect_boundary_frames(shots, frames)
    assert verdict.passed
    assert len(verdict.reasons) == 0


def test_pixel_gate_refuses_incongruent_facial_action():
    shots = [
        {"duration_s": 3.0, "role": "presenter", "expression": "smile of relief and breakthrough"},
    ]
    frames = [
        # Detected AU4 (brow furrow / anger / frustration) instead of smile (AU12)
        {"frame_time_s": 1.5, "au_detected": ["AU4"]},
    ]
    verdict = inspect_boundary_frames(shots, frames)
    assert not verdict.passed
    assert len(verdict.reasons) == 1
    assert "Directed smile/relief" in verdict.reasons[0]
