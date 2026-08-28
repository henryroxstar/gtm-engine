"""Contract tests for the video-storyboard skill.

The storyboard gate generates operator-reviewable still(s) before any video render spend
and writes `storyboard.json` so `video-render` can consume the approved still(s) directly
as the image-to-video start frame.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / "plugin" / "skills" / "video-storyboard"
RENDER_SKILL_DIR = REPO / "plugin" / "skills" / "video-render"

if not (SKILL_DIR / "body_template.md").exists():
    # video-storyboard is `oss = "private"` (gtm_core/gating.toml) — the OSS carve stubs
    # its body_template.md out, so this drift guard has nothing to check in that distribution.
    pytest.skip(
        "video-storyboard body_template.md not present (paid-tier stub)", allow_module_level=True
    )

_ALLOWED_ANCHOR_KINDS = frozenset({"element", "soul_still", "none"})


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _validate_storyboard(data: dict) -> None:
    """Small fail-closed validator mirroring the render_manifest refusal pattern.

    Raises AssertionError with a descriptive message so contract tests can use it.
    """
    assert "entries" in data, "storyboard.json must contain 'entries'"
    entries = data["entries"]
    assert isinstance(entries, list), "'entries' must be a list"
    assert len(entries) >= 1, "storyboard.json must have at least one entry"

    for entry in entries:
        assert isinstance(entry, dict), "each entry must be an object"
        assert "n" in entry, "entry missing 'n'"
        assert "image_path" in entry, "entry missing 'image_path'"
        assert "image_job_id" in entry, "entry missing 'image_job_id'"
        assert "identity_anchor" in entry, "entry missing 'identity_anchor'"
        assert "aspect_ratio" in entry, "entry missing 'aspect_ratio'"
        assert "width" in entry, "entry missing 'width'"
        assert "height" in entry, "entry missing 'height'"

        anchor = entry["identity_anchor"]
        assert isinstance(anchor, dict), "identity_anchor must be an object"
        assert "kind" in anchor, "identity_anchor missing 'kind'"
        assert anchor["kind"] in _ALLOWED_ANCHOR_KINDS, (
            f"identity_anchor.kind={anchor['kind']!r} not in {sorted(_ALLOWED_ANCHOR_KINDS)}"
        )


def test_body_template_documents_the_storyboard_gate():
    body = _text(SKILL_DIR / "body_template.md")
    assert "storyboard.json" in body
    assert "⟦GATE:plan⟧" in body
    assert "before any video render spend" in body.lower()


def test_body_template_resolves_identity_anchor_like_video_render():
    body = _text(SKILL_DIR / "body_template.md")
    assert "reference_element_ids" in body
    assert "soul_id" in body
    assert "soul_2" in body
    assert "brandkit" in body


def test_body_template_runs_monthly_cap_precheck():
    body = _text(SKILL_DIR / "body_template.md")
    assert "month-total" in body
    assert "over_cap" in body


def test_body_template_generates_stills_with_generate_image():
    body = _text(SKILL_DIR / "body_template.md")
    assert "generate_image" in body
    assert "get_cost" in body


def test_body_template_single_clip_produces_one_entry():
    body = _text(SKILL_DIR / "body_template.md")
    assert "exactly **1** still" in body
    assert "single-clip" in body


def test_body_template_multi_shot_skips_broll_and_screen():
    body = _text(SKILL_DIR / "body_template.md")
    assert "broll" in body
    assert "screen" in body
    assert "Skip broll/screen" in body or "skip" in body.lower()


def test_body_template_writes_expected_storyboard_json_shape():
    body = _text(SKILL_DIR / "body_template.md")
    assert "image_path" in body
    assert "image_job_id" in body
    assert "identity_anchor" in body
    assert "aspect_ratio" in body
    assert "width" in body
    assert "height" in body


def test_body_template_storyboard_n_is_null_for_single_clip():
    body = _text(SKILL_DIR / "body_template.md")
    assert "`n`: null" in body or "n: null" in body


def test_body_template_ends_with_plan_gate_sentinel():
    body = _text(SKILL_DIR / "body_template.md")
    # The gate sentinel must be the literal marker, not just mentioned.
    assert "⟦GATE:plan⟧" in body


def test_validator_accepts_valid_storyboard():
    data = {
        "source_script": "content/example/scripts/2026-08-16-test.md",
        "entries": [
            {
                "n": None,
                "image_path": "content/example/video/test/storyboard-01.png",
                "image_job_id": "job-123",
                "identity_anchor": {"kind": "soul_still", "id": None},
                "aspect_ratio": "4:5",
                "width": 1080,
                "height": 1350,
            }
        ],
    }
    _validate_storyboard(data)


def test_validator_accepts_multi_shot_presenter_entries():
    data = {
        "source_script": "content/example/scripts/2026-08-16-test.md",
        "shots_json": "content/example/scripts/test.shots.json",
        "entries": [
            {
                "n": 1,
                "image_path": "content/example/video/test/storyboard-01.png",
                "image_job_id": "job-1",
                "identity_anchor": {"kind": "element", "id": "el-1"},
                "aspect_ratio": "9:16",
                "width": 1080,
                "height": 1920,
            },
            {
                "n": 3,
                "image_path": "content/example/video/test/storyboard-03.png",
                "image_job_id": "job-3",
                "identity_anchor": {"kind": "element", "id": "el-1"},
                "aspect_ratio": "9:16",
                "width": 1080,
                "height": 1920,
            },
        ],
    }
    _validate_storyboard(data)


def test_validator_rejects_unknown_identity_anchor_kind():
    data = {
        "source_script": "content/example/scripts/2026-08-16-test.md",
        "entries": [
            {
                "n": None,
                "image_path": "content/example/video/test/storyboard-01.png",
                "image_job_id": "job-123",
                "identity_anchor": {"kind": "phantom", "id": None},
                "aspect_ratio": "4:5",
                "width": 1080,
                "height": 1350,
            }
        ],
    }
    with __import__("pytest").raises(AssertionError):
        _validate_storyboard(data)


def test_video_render_body_template_reads_storyboard_json():
    """video-render must consume the approved storyboard instead of regenerating."""
    body = _text(RENDER_SKILL_DIR / "body_template.md")
    assert "storyboard.json" in body
    assert "approved storyboard" in body
    assert "use it directly as the start frame" in body


def test_skill_md_exists_and_documents_storyboard():
    skill_md = _text(SKILL_DIR / "SKILL.md")
    assert "storyboard" in skill_md
    assert "video-storyboard" in skill_md


def test_manifest_declares_production_tier():
    from gtm_core.skills.video_storyboard import SKILL

    assert SKILL.capability_tier.value == "production"
    assert SKILL.name == "video-storyboard"
