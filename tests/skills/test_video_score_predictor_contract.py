"""Contract test — video-score actually documents predictor sub-score capture,
calibration banding, and the score.json cross-skill file (Phase 9, §5.8).

The gap this closes: score.json is a NEW cross-skill file contract named in both
video-score's and content-outcomes-sync's bodies, with no schema file to enforce it —
this is the drift guard until/unless one exists.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCORE_DIR = REPO / "plugin" / "skills" / "video-score"
SYNC_DIR = REPO / "plugin" / "skills" / "content-outcomes-sync"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_video_score_body_captures_sub_scores():
    body = _text(SCORE_DIR / "body_template.md")
    for phrase in ("virality index", "hook strength", "retention risk"):
        assert phrase in body, f"video-score body lost its mention of {phrase!r}"


def test_video_score_body_bands_the_recommendation():
    body = _text(SCORE_DIR / "body_template.md")
    assert "predictor_band:high" in body
    assert "predictor_band:medium" in body
    assert "predictor_band:low" in body
    # Named, not vague — the operator-overridable thresholds must be stated.
    assert "70" in body and "40" in body


def test_video_score_body_writes_score_json():
    body = _text(SCORE_DIR / "body_template.md")
    assert "score.json" in body
    assert '"recommended"' in body


def test_video_score_never_treats_the_band_as_a_verdict():
    body = _text(SCORE_DIR / "body_template.md")
    assert "prior" in body
    assert "never quote it as a probability" in body


def test_video_score_skill_md_in_sync():
    skill_md = _text(SCORE_DIR / "SKILL.md")
    assert "predictor_band:high" in skill_md or "predictor_band:high|medium|low" in skill_md
    assert "score.json" in skill_md


def test_video_score_manifest_bumped_for_calibration():
    from gtm_core.skills.video_score import SKILL

    # Pinned as a FLOOR, not an equality — the same correction
    # tests/contracts/test_content_outcomes_sync_phase8.py already made for its own manifest.
    # Calibration landed at 0.4.0 and the manifest may only move forward from there; a later,
    # unrelated bump (0.5.0 added the story_format field to score.json) is not a calibration
    # regression, and an equality made every legitimate bump look like one.
    assert tuple(int(x) for x in SKILL.version.split(".")) >= (0, 4, 0), SKILL.version


def test_content_outcomes_sync_body_reads_score_json_and_tags_the_band():
    body = _text(SYNC_DIR / "body_template.md")
    assert "score.json" in body
    assert "predictor_band:high" in body or "predictor_band:<band>" in body or "band" in body
    assert "--meta" in body


def test_content_outcomes_sync_manifest_bumped_for_calibration():
    """Pins the version as of the calibration-tagging feature landing (0.3.x -> 0.4.0). A later,
    unrelated feature (the X tweet-pattern axis) bumped
    it again to 0.5.0 — this asserts "at least that bump landed and stuck", not "exactly 0.4.0
    forever"."""
    from gtm_core.skills.content_outcomes_sync import SKILL

    assert tuple(int(p) for p in SKILL.version.split(".")) >= (0, 4, 0)


def test_outcomes_cli_exposes_meta_flag_for_the_sub_scores(tmp_path):
    """The body's --meta example must actually be a real, working CLI flag, not
    aspirational documentation."""
    from gtm_core import outcomes as oc

    rc = oc.main(
        [
            "append",
            "--profile",
            "example",
            "--channel",
            "instagram",
            "--outcome",
            "impressions",
            "--value",
            "1",
            "--tag",
            "predictor_band:medium",
            "--meta",
            '{"virality_index": 62, "hook_strength": 58}',
            "--content-root",
            str(tmp_path),
        ]
    )
    assert rc == 0
    rows = oc.read_outcomes(tmp_path, "example")
    assert rows[0]["meta"] == {"virality_index": 62, "hook_strength": 58}
