"""Regression tests protecting established craft and reception invariants (10x Video PRD)."""

from __future__ import annotations

import json
from pathlib import Path

from gtm_core.preview_card import build_preview_card
from gtm_core.reception import CONTROL_ACT_GAP_MAX, MIN_AUDIENCE_SAMPLE
from gtm_core.shots_lint.caption import DESCRIBE_SHARE_MAX, DESCRIBE_SHARE_MAX_PCT
from gtm_core.shots_lint.expression import _lint_expression
from gtm_core.video_lint.pixel_gate import inspect_boundary_frames


def test_stock_reaction_linter_still_fires():
    """R1: The expression linter must still refuse stock emotional reactions (beaming, screaming, sobbing)."""
    shot = {"expression": "beaming as she reads the message", "duration_s": 3.0}
    errors, warnings = [], []
    _lint_expression(shot, "shot[1]", errors, warnings)
    assert len(errors) == 1
    assert "stock reaction" in errors[0]
    assert "beaming" in errors[0]


def test_describe_share_ceiling_unchanged():
    """R2: The 50% describe ceiling from caption-craft.md must survive."""
    assert DESCRIBE_SHARE_MAX == 0.50
    assert DESCRIBE_SHARE_MAX_PCT == 50


def test_preview_card_generation_costs_zero_credits(tmp_path: Path):
    """R3: Generating the preview card must never touch provider APIs or append to costs.jsonl."""
    content_root = tmp_path / "content"
    profile = "test-prof"
    run_dir = content_root / profile / "video" / "test-slug"
    run_dir.mkdir(parents=True)
    (run_dir / "storyboard.json").write_text(json.dumps({"stills": []}), encoding="utf-8")

    card = build_preview_card(profile, "test-slug", content_root=content_root)
    assert card.script_slug == "test-slug"

    costs_file = content_root / profile / "costs.jsonl"
    assert not costs_file.exists()


def test_pixel_gate_returns_verdict_not_exception():
    """R4: Incongruent frames produce a verdict with passed=False, never an exception."""
    shots = [{"duration_s": 3.0, "role": "presenter", "expression": "smile of joy and relief"}]
    frames = [{"frame_time_s": 1.5, "au_detected": ["AU4"]}]
    verdict = inspect_boundary_frames(shots, frames)
    assert isinstance(verdict.passed, bool)
    assert not verdict.passed
    assert len(verdict.reasons) >= 1


def test_reception_constants_unchanged():
    """R5: CONTROL_ACT_GAP_MAX and MIN_AUDIENCE_SAMPLE constants must be preserved."""
    assert CONTROL_ACT_GAP_MAX == 0.15
    assert MIN_AUDIENCE_SAMPLE == 20
