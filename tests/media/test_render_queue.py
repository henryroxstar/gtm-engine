"""Tests for render queue and provider error normalization (10x Video PRD §6)."""

from __future__ import annotations

import time

from gtm_core.render_queue import (
    NORMALIZED_ERROR_CATEGORIES,
    NORMALIZED_STATUSES,
    RenderQueue,
    normalize_error,
    normalize_status,
)


def test_render_queue_normalizes_provider_status():
    """The queue must translate provider-specific statuses to the 5
    normalized states: queued, rendering, done, failed, timeout."""
    assert normalize_status("processing", "heygen") == "rendering"
    assert normalize_status("PENDING", "higgsfield") == "rendering"
    assert normalize_status("waiting", "heygen") == "queued"
    assert normalize_status("completed", "higgsfield") == "done"
    assert normalize_status("failed", "heygen") == "failed"
    assert normalize_status("deadline_exceeded", "reap") == "timeout"

    for status in ("queued", "rendering", "done", "failed", "timeout"):
        assert status in NORMALIZED_STATUSES


def test_render_queue_tracks_elapsed_time():
    """Each queue entry must report elapsed_s from submission."""
    queue = RenderQueue()
    entry = queue.submit("shot-1", provider="higgsfield", credits=15)
    time.sleep(0.05)
    assert entry.elapsed_s >= 0.04
    assert entry.status == "queued"

    entry.update_status("processing")
    assert entry.status == "rendering"


def test_provider_error_normalization():
    """Provider-specific error codes must map to exactly 5 categories:
    insufficient_credits, rate_limited, content_policy, provider_error, timeout."""
    errors = {
        "HeyGen V-2": "provider_error",
        "Higgsfield 429": "rate_limited",
        "Higgsfield content_flagged": "content_policy",
        "HeyGen insufficient_balance": "insufficient_credits",
        "Reap timeout": "timeout",
    }
    for raw, expected in errors.items():
        assert normalize_error(raw) == expected
        assert expected in NORMALIZED_ERROR_CATEGORIES


def test_raw_error_preserved_in_debug_log():
    """The raw provider error must be logged for debugging even though
    only the normalized category reaches the operator."""
    queue = RenderQueue()
    entry = queue.submit("shot-2", provider="heygen")
    normalized = entry.record_error("HeyGen V-2: avatar rendering pipeline internal error")

    assert normalized == "provider_error"
    assert entry.raw_error == "HeyGen V-2: avatar rendering pipeline internal error"
    assert any("HeyGen V-2" in log for log in entry.debug_log)

    # Operator cockpit summary should not contain the vendor code "V-2" or internal trace
    summary = entry.to_operator_summary()
    assert "V-2" not in summary
    assert "Provider error" in summary
