"""Tests for known flicker profile registry (10x Video PRD §6)."""

from __future__ import annotations

from gtm_core.render_queue import check_known_flicker


def test_known_flicker_warning_at_preflight():
    """A shot duration exceeding a provider's known flicker threshold must produce a preflight advisory."""
    advisory = check_known_flicker("luma_dream_machine", duration_s=7.0)
    assert advisory is not None
    assert "known to flicker" in advisory
    assert "5.0s" in advisory
    assert "7.0s" in advisory


def test_known_flicker_passes_under_threshold():
    """A shot duration within threshold produces no flicker advisory."""
    advisory = check_known_flicker("luma_dream_machine", duration_s=3.0)
    assert advisory is None


def test_unknown_provider_has_no_flicker_warning():
    """A provider not in the registry produces no flicker advisory."""
    advisory = check_known_flicker("unknown_new_engine", duration_s=10.0)
    assert advisory is None
