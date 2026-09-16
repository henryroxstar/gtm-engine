"""Tests for review.json structure and description-only posture (10x Video PRD §5 / Q4)."""

from __future__ import annotations

from pathlib import Path

from gtm_core.review_json import (
    EVALUATIVE_WORDS,
    ReviewManifest,
    build_review_entry,
)


def test_review_json_contains_directed_and_observed_columns():
    """Each entry in review.json must carry directed expression, observed description, and delta."""
    entry = build_review_entry(
        shot_index=1,
        directed_expression="brow lowerer (AU4) with jaw clench",
        vlm_description="AU4 brow lowerer visible, slight jaw tension",
    )
    d = entry.to_dict()
    assert "directed" in d
    assert "observed" in d
    assert "delta" in d
    assert d["shot_index"] == 1
    assert d["verdict"] is None


def test_review_json_does_not_contain_evaluation():
    """The VLM prompt asks 'describe', never 'evaluate'. The output must
    not contain evaluative words (good, bad, correct, wrong)."""
    vlm_raw = "The face is very good and correct, displaying AU12 lip corner pull."
    entry = build_review_entry(
        shot_index=2,
        directed_expression="lip corner pull (AU12)",
        vlm_description=vlm_raw,
    )
    observed_words = {w.lower().strip(".,!?:;") for w in entry.observed.split()}
    for bad_word in EVALUATIVE_WORDS:
        assert bad_word not in observed_words


def test_review_manifest_writes_clean_json(tmp_path: Path):
    """ReviewManifest writes formatted review.json file."""
    entries = [
        build_review_entry(1, "neutral eye level gaze", "neutral gaze forward"),
        build_review_entry(2, "slight smile of relief", "lip corner pull AU12"),
    ]
    manifest = ReviewManifest(run_id="run-film-01", entries=entries)
    out_file = tmp_path / "content" / "acme" / "video" / "run-film-01" / "review.json"
    manifest.write(out_file)

    assert out_file.is_file()
    import json

    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["run_id"] == "run-film-01"
    assert len(data["entries"]) == 2
