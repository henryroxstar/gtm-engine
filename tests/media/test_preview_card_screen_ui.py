"""Tests for gtm_core.preview_card with screen_ui shots (zero provider credits)."""

from __future__ import annotations

import json
from pathlib import Path

from gtm_core.preview_card import build_preview_card


def test_preview_card_all_screen_ui_shots_cost_zero_credits(tmp_path: Path):
    root = tmp_path / "content"
    prof = "test-prof"
    slug = "2026-10-16-screen-ui-video"

    run_dir = root / prof / "video" / slug
    run_dir.mkdir(parents=True)
    scripts_dir = root / prof / "scripts"
    scripts_dir.mkdir(parents=True)

    sb_data = {
        "stills": [
            {"path": "content/test-prof/video/slug/frame_01.png"},
            {"path": "content/test-prof/video/slug/frame_02.png"},
            {"path": "content/test-prof/video/slug/frame_03.png"},
        ]
    }
    (run_dir / "storyboard.json").write_text(json.dumps(sb_data), encoding="utf-8")

    shots_data = {
        "shots": [
            {
                "duration_s": 3.0,
                "caption": "First hook",
                "role": "screen",
                "production": {"screen_ui": {"scene": "kinetic-type", "headline": "Security"}},
            },
            {
                "duration_s": 4.0,
                "caption": "Product walkthrough",
                "role": "screen",
                "production": {"screen_ui": {"scene": "phone-walkthrough"}},
            },
            {
                "duration_s": 3.0,
                "caption": "Closing claim",
                "role": "screen",
                "production": {"screen_ui": {"scene": "hero-reveal"}},
            },
        ]
    }
    (scripts_dir / f"{slug}.shots.json").write_text(json.dumps(shots_data), encoding="utf-8")

    card = build_preview_card(prof, slug, content_root=root)
    assert card.estimated_credits == 0
    assert card.estimated_credits_min == 0

    md = card.to_markdown()
    assert "**Spend Commitment:** ~0 credits" in md
    assert "Approving proceeds to render (0 provider credits); Edit to revise script." in md

    # Invariant I4: No internal engine jargon leaked into user-facing markdown
    for forbidden in (
        "backend",
        "hyperframes",
        "critique.json",
        "screen-ui-build.json",
        "data-no-timeline",
    ):
        assert forbidden not in md, f"Forbidden term {forbidden!r} found in PreviewCard markdown"


def test_preview_card_mixed_screen_ui_and_provider_shots(tmp_path: Path):
    root = tmp_path / "content"
    prof = "test-prof"
    slug = "2026-10-16-mixed-video"

    run_dir = root / prof / "video" / slug
    run_dir.mkdir(parents=True)
    scripts_dir = root / prof / "scripts"
    scripts_dir.mkdir(parents=True)

    shots_data = {
        "shots": [
            {
                "duration_s": 3.0,
                "caption": "Presenter talking",
                "role": "presenter",
            },
            {
                "duration_s": 4.0,
                "caption": "Product walkthrough",
                "role": "screen",
                "production": {"screen_ui": {"scene": "phone-walkthrough"}},
            },
        ]
    }
    (scripts_dir / f"{slug}.shots.json").write_text(json.dumps(shots_data), encoding="utf-8")

    card = build_preview_card(prof, slug, content_root=root)
    # 1 provider shot: 5 credits max, 2.5 (rounded to 2 or 3) credits min
    assert card.estimated_credits == 5
    assert card.estimated_credits_min in (2, 3)

    md = card.to_markdown()
    assert "**Spend Commitment:** ~" in md
    assert "Approving commits render spend; Edit to revise script." in md
