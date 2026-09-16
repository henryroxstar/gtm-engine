"""Tests for gtm_core.preview_card."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core.preview_card import build_preview_card


def test_build_preview_card_structure(tmp_path: Path):
    root = tmp_path / "content"
    prof = "test-prof"
    slug = "2026-09-12-test-feature"

    run_dir = root / prof / "video" / slug
    run_dir.mkdir(parents=True)
    scripts_dir = root / prof / "scripts"
    scripts_dir.mkdir(parents=True)

    # Fake storyboard.json
    sb_data = {
        "stills": [
            {"path": "content/test-prof/video/slug/frame_01.png"},
            {"path": "content/test-prof/video/slug/frame_02.png"},
            {"path": "content/test-prof/video/slug/frame_03.png"},
            {"path": "content/test-prof/video/slug/frame_04.png"},
        ]
    }
    (run_dir / "storyboard.json").write_text(json.dumps(sb_data), encoding="utf-8")

    # Fake shots.json
    shots_data = {
        "shots": [
            {"duration_s": 3.0, "caption": "Hello world hook"},
            {"duration_s": 4.0, "caption": "Demonstrating the solution"},
            {"duration_s": 3.0, "caption": "Payoff statement here"},
        ]
    }
    (scripts_dir / f"{slug}.shots.json").write_text(json.dumps(shots_data), encoding="utf-8")

    card = build_preview_card(prof, slug, content_root=root)
    assert card.script_slug == slug
    assert len(card.hero_stills) == 3
    assert card.total_duration_s == 10.0
    assert card.total_words == 9
    assert "Pacing clean" in card.pacing_verdict

    md = card.to_markdown()
    assert "Unified Preview Card" in md
    assert "1. Hook" in md
    assert "2. Middle" in md
    assert "3. Payoff" in md
    assert "Spend Commitment" in md


def test_render_spend_guard_refuses_without_storyboard_approval(tmp_path: Path):
    """Zero video diffusion spend permitted without prior approved storyboard.json."""
    from gtm_core.render_manifest import ManifestError, _validate_storyboard

    repo = tmp_path / "repo"
    sb_path = repo / "content" / "acme" / "video" / "test-run" / "storyboard.json"
    sb_path.parent.mkdir(parents=True)

    # 1. Missing storyboard raises ManifestError
    with pytest.raises(ManifestError, match="names a file that does not exist"):
        _validate_storyboard(str(sb_path), synthetic=True, repo_root=repo)

    # 2. Unapproved storyboard raises ManifestError
    sb_path.write_text(json.dumps({"entries": [{"image_path": "frame.png"}], "approved": False}))
    with pytest.raises(ManifestError, match="is not marked approved"):
        _validate_storyboard(str(sb_path), synthetic=True, repo_root=repo)

    # 3. Approved storyboard passes validation
    sb_path.write_text(json.dumps({"entries": [{"image_path": "frame.png"}], "approved": True}))
    _validate_storyboard(str(sb_path), synthetic=True, repo_root=repo)


def test_preview_card_cli_positional_and_option(tmp_path: Path):
    from gtm_core import preview_card as pc

    root = tmp_path / "content"
    prof = "acme"
    slug = "test-slug"
    run_dir = root / prof / "video" / slug
    run_dir.mkdir(parents=True)

    # Positional
    assert pc.main(["test-slug", "--profile", prof]) == 0
    # Option
    assert pc.main(["--script-slug", "test-slug", "--profile", prof, "--json"]) == 0
