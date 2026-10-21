"""Tests for prompt decay tracking and seed stability regression (10x Video PRD §5)."""

from __future__ import annotations

from gtm_core.review_json import (
    build_review_entry,
    check_seed_stability,
    log_prompt_decay,
)


def test_prompt_decay_log_covers_all_shots():
    """The decay log must have one entry per shot with {shot_index, delta_score}."""
    entries = [
        build_review_entry(1, "brows lifted", "brows lifted"),
        build_review_entry(2, "brows lifted", "eyes wide"),
        build_review_entry(3, "brows lifted", "neutral face"),
        build_review_entry(4, "brows lifted", "unrelated action"),
        build_review_entry(5, "brows lifted", "flat expression"),
        build_review_entry(6, "brows lifted", "no movement"),
    ]

    decay_log = log_prompt_decay(entries)
    assert len(decay_log) == 6
    for idx, item in enumerate(decay_log, 1):
        assert item["shot_index"] == idx
        assert "delta_score" in item
        assert 0.0 <= item["delta_score"] <= 1.0


def test_seed_stability_same_prompt_same_seed_same_output():
    """For providers supporting seed reuse, identical prompt+seed must
    produce identical frame hashes within tolerance."""
    hash_run_1 = "a1b2c3d4e5f67890"
    hash_run_2 = "a1b2c3d4e5f67890"
    assert check_seed_stability(hash_run_1, hash_run_2)

    # Drift within 1-char Hamming tolerance
    drift_hash = "a1b2c3d4e5f67891"
    assert check_seed_stability(hash_run_1, drift_hash, max_drift_hamming=1)
    assert not check_seed_stability(hash_run_1, drift_hash, max_drift_hamming=0)

    # Big drift fails
    bad_hash = "ffffffffffffffff"
    assert not check_seed_stability(hash_run_1, bad_hash, max_drift_hamming=1)
