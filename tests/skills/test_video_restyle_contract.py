"""Contract test — video-restyle actually documents the interactive-first, exact-cost,
and 720p-constraint guardrails (Phase 11, §5.7).

The gap this closes: shorts_studio_create takes an UPLOADED source video via a browser
widget — there is no headless path — so this skill's Step 0 must refuse to fake an
upload rather than silently degrading. This is the drift guard for that refusal, the
restyle_preset_id dependency on identity-kit, and the exact get_cost preflight (the one
lane in this pack where preflight IS exact, unlike video-clip's honest estimate).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RESTYLE_DIR = REPO / "plugin" / "skills" / "video-restyle"

if not (RESTYLE_DIR / "body_template.md").exists():
    # video-restyle is `oss = "private"` (gtm_core/gating.toml) — the OSS carve stubs its
    # body_template.md out, so this drift guard has nothing to check in that distribution.
    pytest.skip(
        "video-restyle body_template.md not present (paid-tier stub)", allow_module_level=True
    )


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_body_template_references_shorts_studio_tools():
    body = _text(RESTYLE_DIR / "body_template.md")
    assert "shorts_studio_create" in body
    assert "shorts_studio_status" in body


def test_body_template_is_interactive_first_by_construction():
    body = _text(RESTYLE_DIR / "body_template.md")
    assert "media_upload_widget" in body
    assert "browser-side" in body
    assert "stop here and say so" in body


def test_body_template_depends_on_restyle_preset_id():
    body = _text(RESTYLE_DIR / "body_template.md")
    assert "restyle_preset_id" in body
    assert "identity-kit" in body


def test_body_template_preflight_is_exact_not_estimated():
    body = _text(RESTYLE_DIR / "body_template.md")
    assert "get_cost: true" in body
    assert "duration_seconds" in body
    assert "exact" in body.lower()


def test_body_template_states_the_720p_constraint():
    body = _text(RESTYLE_DIR / "body_template.md")
    assert "720p" in body
    assert "not hiding it" in body or "not a bug" in body


def test_body_template_notes_article_50_even_without_an_identity_handle():
    body = _text(RESTYLE_DIR / "body_template.md")
    assert "Article 50" in body
    assert "manipulated" in body


def test_skill_md_in_sync():
    skill_md = _text(RESTYLE_DIR / "SKILL.md")
    assert "shorts_studio_create" in skill_md
    assert "720p" in skill_md


def test_manifest_is_production_tier_phase_11():
    from gtm_core.skills.video_restyle import SKILL

    assert SKILL.phase == "11"
    assert SKILL.name == "video-restyle"


def test_skill_is_registered_and_discoverable():
    from gtm_core.skills.registry import all_skills

    assert "video-restyle" in {s.name for s in all_skills()}


def test_body_template_records_identity_used_as_empty():
    """Phase 12: a restyle preset is a visual look, not a soul/element/voice handle
    — identity_used is stated explicitly as [] here too, even though Step 7 still
    flags the Article 50 duty for AI-manipulated media regardless."""
    body = _text(RESTYLE_DIR / "body_template.md")
    assert "identity_used" in body
    assert "[]" in body


def test_content_plan_body_tolerates_a_radar_less_invocation():
    """The restyle lane's plan node has no upstream radar — content-plan's shared
    body must not hard-block on missing radar clusters for this case."""
    body = _text(REPO / "plugin" / "skills" / "content-plan" / "body_template.md")
    assert "restyle" in body.lower()
    assert "node prompt" in body
