"""Contract test — video-script's long-form shot-list output.

video-script had no dedicated contract test before this. This guards the pieces
video-render's multi-shot branch depends on: the [CAMERA] beat tag, the
duration-ceiling-aware shot-splitting rule, and the shots.json write step whose
presence is the switch video-render keys off.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / "plugin" / "skills" / "video-script"

if not (SKILL_DIR / "body_template.md").exists():
    # video-script is `oss = "private"` (gtm_core/gating.toml) — the OSS carve stubs its
    # body_template.md out, so this drift guard has nothing to check in that distribution.
    pytest.skip(
        "video-script body_template.md not present (paid-tier stub)", allow_module_level=True
    )


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_body_template_documents_the_camera_tag():
    body = _text(SKILL_DIR / "body_template.md")
    assert "[CAMERA]" in body
    assert "never left implicit" in body


def test_body_template_documents_shot_splitting_against_the_duration_ceiling():
    body = _text(SKILL_DIR / "body_template.md")
    assert "provider-duration-ceilings.md" in body
    assert "Split a beat that would run long" in body


def test_body_template_documents_the_shots_json_write_step():
    body = _text(SKILL_DIR / "body_template.md")
    assert "shots.json" in body
    assert "style_scaffold" in body
    assert "Step 1.5" in body


def test_body_template_shot_list_schema_has_the_required_fields():
    body = _text(SKILL_DIR / "body_template.md")
    for field in ("source_item", "total_duration_s", "provider_model", "motion_prompt", "camera"):
        assert field in body


def test_body_template_absent_shots_json_is_the_single_clip_switch():
    body = _text(SKILL_DIR / "body_template.md")
    assert "Skip this step entirely for a normal single-clip short" in body


def test_body_template_never_spends_a_credit_still_holds_for_shot_lists():
    """The shot list is free planning output — the existing 'never call a
    generation tool' guardrail must still cover it, not get quietly narrowed."""
    body = _text(SKILL_DIR / "body_template.md")
    assert "Never** call an image or video generation tool" in body


def test_body_template_reports_shot_count_and_wallclock():
    body = _text(SKILL_DIR / "body_template.md")
    assert "shot count" in body
    assert "wall-clock" in body


def test_skill_md_documents_the_camera_tag():
    skill_md = _text(SKILL_DIR / "SKILL.md")
    assert "[CAMERA]" in skill_md


def test_manifest_bumped_past_0_1_0_for_the_shot_list():
    from gtm_core.skills.video_script import SKILL

    assert SKILL.version != "0.1.0"
