"""Tests for tempo bounce defaults, bounds validation, and CSS spring easing tokens.

Covers Test Plan T6 and T7.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gtm_core.brandkit_css import render_tokens_css
from gtm_core.design_tokens import TEMPOS, css_spring_linear, resolve_motion

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "profiles"


def test_tempo_bounce_defaults():
    """T6: TEMPOS dataclass carries explicit bounce values."""
    assert TEMPOS["hold"].bounce == 0.0
    assert TEMPOS["brisk"].bounce == 0.15
    assert TEMPOS["burst"].bounce == 0.25


def test_resolve_motion_bounce_bounds_and_fallbacks():
    """T6: resolve_motion validates bounce in [0.0, 1.0] and falls back gracefully."""
    # Valid explicit bounce
    assert resolve_motion({"motion": {"bounce": 0.4}}).bounce == pytest.approx(0.4)
    assert resolve_motion({"motion": {"bounce": 0.0}}).bounce == pytest.approx(0.0)
    assert resolve_motion({"motion": {"bounce": 1.0}}).bounce == pytest.approx(1.0)

    # Out of bounds falls back to tempo default (brisk = 0.15)
    assert resolve_motion({"motion": {"bounce": -0.1}}).bounce == pytest.approx(0.15)
    assert resolve_motion({"motion": {"bounce": 1.5}}).bounce == pytest.approx(0.15)

    # Invalid types fall back gracefully without raising
    assert resolve_motion({"motion": {"bounce": "fast"}}).bounce == pytest.approx(0.15)
    assert resolve_motion({"motion": {"bounce": None}}).bounce == pytest.approx(0.15)
    assert resolve_motion({"motion": {"bounce": True}}).bounce == pytest.approx(0.15)

    # Fallback uses active tempo default
    assert resolve_motion(
        {"motion": {"default_tempo": "hold", "bounce": "invalid"}}
    ).bounce == pytest.approx(0.0)


def test_css_spring_linear_monotone_critically_damped():
    """T7: css_spring_linear at bounce=0.0 produces monotonic non-decreasing coordinates in [0.0, 1.0]."""
    res = css_spring_linear(0.55, bounce=0.0, points=32)
    assert res.startswith("linear(") and res.endswith(")")
    raw_coords = [c.strip() for c in res[7:-1].split(",")]
    assert len(raw_coords) == 32

    # Verify 4-decimal precision on every point
    for c in raw_coords:
        assert re.fullmatch(r"-?\d+\.\d{4}", c), f"Expected 4 decimals, got {c}"

    values = [float(c) for c in raw_coords]
    assert values[0] == pytest.approx(0.0)
    assert values[-1] == pytest.approx(1.0)

    # Monotonically non-decreasing
    for i in range(len(values) - 1):
        assert values[i] <= values[i + 1] + 1e-6, (
            f"Not monotonic at {i}: {values[i]} > {values[i + 1]}"
        )
        assert 0.0 <= values[i] <= 1.0


def test_css_spring_linear_overshoot_underdamped():
    """T7: css_spring_linear at bounce=0.25 overshoots 1.0."""
    res = css_spring_linear(0.55, bounce=0.25, points=32)
    raw_coords = [c.strip() for c in res[7:-1].split(",")]
    values = [float(c) for c in raw_coords]

    assert max(values) > 1.0, f"Expected overshoot > 1.0, got max {max(values)}"
    assert values[0] == pytest.approx(0.0)
    assert values[-1] == pytest.approx(1.0)


def test_css_spring_linear_duration_validation():
    """T7: css_spring_linear raises ValueError on duration_s <= 0."""
    with pytest.raises(ValueError, match="duration_s must be positive"):
        css_spring_linear(0.0, bounce=0.15)
    with pytest.raises(ValueError, match="duration_s must be positive"):
        css_spring_linear(-1.0, bounce=0.15)


def test_render_tokens_css_emits_motion_tokens_and_arrive_easing():
    """T7: render_tokens_css emits --motion-bounce, --easing-spring, and sets --video-easing-arrive."""
    kit = {
        "palette": {"canvas": "#000000", "surface": "#111111", "primary": "#ffffff"},
        "motion": {"bounce": 0.00, "default_tempo": {"duration_s": 0.7, "stagger_s": 0.09}},
    }
    css = render_tokens_css(kit)
    assert "--motion-bounce: 0.00;" in css
    assert "--video-easing-arrive: cubic-bezier(0.22, 1, 0.36, 1);" in css
    assert "--easing-spring: linear(" in css
    assert "--ease-spring: var(--easing-spring);" in css
