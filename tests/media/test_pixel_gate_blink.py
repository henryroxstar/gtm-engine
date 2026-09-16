"""Tests for blink-rate heuristic in pixel_gate (10x Video PRD §4)."""

from __future__ import annotations

from gtm_core.video_lint.pixel_gate import inspect_boundary_frames


def test_pixel_gate_flags_zero_blinks_on_long_avatar_render():
    """A 5+ second avatar render with zero detected blinks must be flagged as review: static_gaze."""
    shots = [{"duration_s": 6.0, "role": "presenter", "expression": "warm"}]
    frames = [
        {"frame_time_s": t, "au_detected": [], "blink": False} for t in [1.0, 2.0, 3.0, 4.0, 5.0]
    ]
    verdict = inspect_boundary_frames(shots, frames)
    assert not verdict.passed
    assert any("static_gaze" in r for r in verdict.reasons)


def test_pixel_gate_passes_render_with_blinks():
    """A render with detected blinks is not flagged."""
    shots = [{"duration_s": 6.0, "role": "presenter", "expression": "warm"}]
    frames = [
        {"frame_time_s": 1.0, "au_detected": [], "blink": False},
        {"frame_time_s": 2.5, "au_detected": ["AU45"], "blink": True},
        {"frame_time_s": 4.0, "au_detected": [], "blink": False},
    ]
    verdict = inspect_boundary_frames(shots, frames)
    assert verdict.passed
    assert not any("static_gaze" in r for r in verdict.reasons)


def test_pixel_gate_short_shots_do_not_require_blinks():
    """Shots shorter than 5 seconds are not flagged for absent blinks."""
    shots = [{"duration_s": 3.0, "role": "presenter", "expression": "warm"}]
    frames = [
        {"frame_time_s": 1.0, "au_detected": [], "blink": False},
        {"frame_time_s": 2.0, "au_detected": [], "blink": False},
    ]
    verdict = inspect_boundary_frames(shots, frames)
    assert verdict.passed
