"""Contract test — video-clip actually documents the repurpose lane's honest-cost
and ownership guardrails (Phase 10, §5.7; repointed to Reap in Phase D).

The gap this closes: Reap's create_clips has NO cost/balance tool anywhere in its
24-tool surface (§4.2 capability audit, extended in Phase D) — a strictly worse
position than the Higgsfield personal_clipper this skill used to call, which at
least had `balance`. This is the drift guard for that honesty, the two-step-confirm
handling (silent error #12), the immediate cost-row-on-submit discipline, the
ownership-warning and never-resubmit guardrails, and the video-score triage-mode
wiring on the other end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CLIP_DIR = REPO / "plugin" / "skills" / "video-clip"
SCORE_DIR = REPO / "plugin" / "skills" / "video-score"

if not (CLIP_DIR / "body_template.md").exists():
    # video-clip is `oss = "private"` (gtm_core/gating.toml) — the OSS carve stubs its
    # body_template.md out, so this drift guard has nothing to check in that distribution.
    pytest.skip("video-clip body_template.md not present (paid-tier stub)", allow_module_level=True)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_body_template_references_the_reap_clip_tools():
    body = _text(CLIP_DIR / "body_template.md")
    assert "create_clips" in body
    assert "get_status" in body
    assert "get_results" in body
    assert "list_videos" in body


def test_body_template_no_longer_references_higgsfield_personal_clipper():
    body = _text(CLIP_DIR / "body_template.md")
    assert "personal_clipper" not in body


def test_body_template_documents_the_two_step_confirm_flow():
    body = _text(CLIP_DIR / "body_template.md")
    assert "confirmationToken" in body
    assert "plannedSettings" in body
    assert "no project id" in body or "not a success" in body


def test_body_template_documents_no_cost_tool_and_writes_cost_row_on_submit():
    body = _text(CLIP_DIR / "body_template.md")
    assert "no `balance` or `get_cost` tool" in body or "no balance" in body.lower()
    assert "append-cost" in body
    assert "source_duration_s" in body
    # The cost row lands on SUBMIT, not on completion — the point of Step 5.
    assert "not** after" in body or "not after" in body


def test_body_template_checks_a_unit_count_cap_not_a_dollar_cap():
    body = _text(CLIP_DIR / "body_template.md")
    assert "month-units" in body
    assert "media_credits" in body


def test_body_template_uses_a_neutral_uncbranded_caption_preset():
    body = _text(CLIP_DIR / "body_template.md")
    assert "get_caption_styles" in body
    assert "neutral" in body.lower()
    assert "captionsPreset" in body


def test_body_template_warns_before_spending_on_a_non_owned_url():
    body = _text(CLIP_DIR / "body_template.md")
    assert "own channel" in body
    assert "before spending" in body


def test_body_template_never_resubmits_into_a_new_job():
    body = _text(CLIP_DIR / "body_template.md")
    assert "never resubmit" in body.lower() or "Never resubmit" in body


def test_body_template_writes_render_clips_manifest():
    body = _text(CLIP_DIR / "body_template.md")
    assert "render-clips.json" in body
    assert "content/<active>/video/clips/" in body


def test_skill_md_in_sync():
    skill_md = _text(CLIP_DIR / "SKILL.md")
    assert "create_clips" in skill_md
    assert "personal_clipper" not in skill_md


def test_manifest_is_production_tier_phase_10():
    from gtm_core.skills.video_clip import SKILL

    assert SKILL.phase == "10"
    assert SKILL.name == "video-clip"
    assert SKILL.version == "0.4.0"


def test_skill_is_registered_and_discoverable():
    from gtm_core.skills.registry import all_skills

    assert "video-clip" in {s.name for s in all_skills()}


def test_video_score_documents_triage_mode():
    body = _text(SCORE_DIR / "body_template.md")
    assert "Triage mode" in body
    assert "render-clips.json" in body
    assert "top N" in body
    assert "archive" in body.lower()


def test_body_template_records_identity_used_as_empty():
    """Phase 12: repurposed real footage never carries a synthetic identity handle —
    the field is stated explicitly as [], not omitted."""
    body = _text(CLIP_DIR / "body_template.md")
    assert "identity_used" in body
    assert "[]" in body
