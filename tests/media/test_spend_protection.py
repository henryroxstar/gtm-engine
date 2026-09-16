"""Tests for spend protection and credit ceiling enforcement (10x Video PRD §1)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gtm_core.preview_card import PreviewCard, build_preview_card
from gtm_core.spend_protection import (
    SpendCeilingExceeded,
    check_spend_ceiling,
    evaluate_preflight_spend,
    get_cumulative_run_spend,
    log_provider_call_cost,
)


def test_preflight_refuses_when_cumulative_spend_exceeds_ceiling(tmp_path: Path):
    """A run whose accumulated credits exceed its preset's max_spend_credits must be refused BEFORE provider call."""
    content_root = tmp_path / "content"
    profile = "test-prof"
    run_id = "run-001"

    # Seed costs.jsonl with 48 credits
    log_provider_call_cost(
        content_root,
        profile,
        run_id=run_id,
        shot_index=1,
        provider="higgsfield",
        credits=48,
    )

    provider_mock = MagicMock()

    # Next call costs 5 credits against ceiling of 50 -> total 53 > 50 -> must refuse
    with pytest.raises(SpendCeilingExceeded) as exc_info:
        evaluate_preflight_spend(
            profile,
            preset_key="explainer-breakdown",
            run_id=run_id,
            next_call_credits=5,
            content_root=content_root,
            ceiling_override=50,
        )
        provider_mock("render")

    provider_mock.assert_not_called()
    assert "exceeding ceiling of 50" in str(exc_info.value)


def test_preflight_allows_when_spend_is_within_ceiling(tmp_path: Path):
    """A run within budget proceeds normally."""
    content_root = tmp_path / "content"
    profile = "test-prof"
    run_id = "run-002"

    # Seed costs.jsonl with 10 credits
    log_provider_call_cost(
        content_root,
        profile,
        run_id=run_id,
        shot_index=1,
        provider="higgsfield",
        credits=10,
    )

    provider_mock = MagicMock()

    # Next call costs 5 credits against ceiling of 50 -> total 15 <= 50 -> allowed
    evaluate_preflight_spend(
        profile,
        preset_key="explainer-breakdown",
        run_id=run_id,
        next_call_credits=5,
        content_root=content_root,
        ceiling_override=50,
    )
    provider_mock("render")

    provider_mock.assert_called_once_with("render")


def test_spend_ceiling_is_deterministic_not_model_judgement():
    """The ceiling check is pure arithmetic, never a model call."""
    # check_spend_ceiling should raise SpendCeilingExceeded without network or LLM
    with pytest.raises(SpendCeilingExceeded):
        check_spend_ceiling(current_spend=45, next_call_credits=10, ceiling=50)

    # Within bounds: should pass cleanly
    check_spend_ceiling(current_spend=30, next_call_credits=15, ceiling=50)


def test_each_provider_call_appends_cost_entry(tmp_path: Path):
    """Every provider API call during a render run must append exactly one
    entry to costs.jsonl with {run_id, shot_index, provider, credits, timestamp}."""
    content_root = tmp_path / "content"
    profile = "test-prof"
    run_id = "run-003"

    for i in range(1, 4):
        log_provider_call_cost(
            content_root,
            profile,
            run_id=run_id,
            shot_index=i,
            provider="higgsfield",
            credits=15,
            timestamp=f"2026-09-12T10:0{i}:00Z",
        )

    costs_file = content_root / profile / "costs.jsonl"
    assert costs_file.is_file()

    lines = [
        json.loads(line)
        for line in costs_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 3
    for idx, row in enumerate(lines, 1):
        assert row["run_id"] == run_id
        assert row["shot_index"] == idx
        assert row["provider"] == "higgsfield"
        assert row["credits"] == 15
        assert "ts" in row

    # Cumulative spend helper verifies total sum
    assert get_cumulative_run_spend(content_root, profile, run_id) == 45


def test_cost_tally_is_readable_at_preview_gate(tmp_path: Path):
    """The preview card must include a human-readable credit budget line."""
    card = PreviewCard(
        script_slug="test-script",
        hero_stills=["still1.jpg", "still2.jpg", "still3.jpg"],
        animatic_path=None,
        total_duration_s=15.0,
        total_words=30,
        pacing_verdict="Pacing clean",
        estimated_credits=23,
        estimated_credits_min=11,
        budget_credits=50,
    )
    md = card.to_markdown()
    assert "Credit budget:" in md
    assert "23 / 50 credits" in md


def test_preview_stills_do_not_count_against_spend_ceiling(tmp_path: Path):
    """Hero stills (image generation) are zero-credit and must not increment costs.jsonl."""
    content_root = tmp_path / "content"
    profile = "test-prof"
    run_dir = content_root / profile / "video" / "test-slug"
    run_dir.mkdir(parents=True)
    sb_file = run_dir / "storyboard.json"
    sb_file.write_text(
        json.dumps(
            {
                "stills": [
                    {"path": "frame1.png"},
                    {"path": "frame2.png"},
                    {"path": "frame3.png"},
                ]
            }
        ),
        encoding="utf-8",
    )

    card = build_preview_card(profile, "test-slug", content_root=content_root)
    assert len(card.hero_stills) == 3

    costs_file = content_root / profile / "costs.jsonl"
    assert not costs_file.exists(), (
        "costs.jsonl must not be created or written during preview generation"
    )
