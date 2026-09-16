"""Contract tests for the video-router skill.

The router asks one plain-English question and dispatches to the correct creator pack
variant. It is not a graph node and spends no credits.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / "plugin" / "skills" / "video-router"

if not (SKILL_DIR / "body_template.md").exists():
    # video-router is `oss = "private"` (gtm_core/gating.toml) — the OSS carve stubs its
    # body_template.md out, so this drift guard has nothing to check in that distribution.
    pytest.skip(
        "video-router body_template.md not present (paid-tier stub)", allow_module_level=True
    )


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_body_template_offers_verified_lanes_rather_than_asking_an_unchecked_question():
    """Superseded 2026-08-28 the old "do you have footage already?" question.

    That question offered lanes whose feasibility nothing had checked, which is how a session
    selected a lane that could not run — after the concept had been written twice. Both halves
    are still offered; what changed is that the offer is now the preflight's verdict rather than
    a menu the skill hopes is accurate.
    """
    body = _text(SKILL_DIR / "body_template.md")
    lower = body.lower()
    assert "do not ask a question whose answers you have not verified" in lower
    assert "footage" in lower and "from scratch" in lower
    assert "ready" in lower and "unlock" in lower


def test_body_template_runs_the_lane_preflight_before_anything_else():
    """Blocks A/B before Block C. The ordering IS the fix — creative written first and validated
    after is what made every revision constraint-discovery leaking backward."""
    body = _text(SKILL_DIR / "body_template.md")
    assert "gtm_core.video_preflight" in body
    step0 = body.split("## Step 0.5")[0]
    assert "video_preflight" in step0, "the preflight must run in Step 0, before the lane offer"


def test_body_template_keys_identity_handles_to_the_lane_not_a_fixed_pair():
    """The exact defect that let the old Step 0 pass on a profile that could not render.

    `presenter-video` renders on HeyGen; auditing soul_id/voice_id says nothing about it.
    """
    body = _text(SKILL_DIR / "body_template.md")
    assert "heygen_avatar_id" in body and "heygen_voice_grade" in body
    assert "keyed to the LANE" in body


def test_body_template_makes_the_faceless_lane_the_cold_start_default():
    """A new profile has no footage and no handles by definition — day one for every tenant."""
    body = _text(SKILL_DIR / "body_template.md")
    lower = body.lower()
    assert "default_lane" in body
    assert "progressive" in lower, "identity must be progressive, not front-loaded"


def test_body_template_reports_an_unmatched_request_as_a_gap():
    """Not every shape of video work has a graph. Bending one into the nearest lane burns two
    operator gates before failing at the first node that spends."""
    body = _text(SKILL_DIR / "body_template.md")
    assert "matches no lane" in body.lower() or "matches NO lane" in body


def test_body_template_routes_from_scratch_to_short_form_video():
    body = _text(SKILL_DIR / "body_template.md")
    assert "short-form-video" in body
    assert "from scratch" in body.lower()


def test_body_template_routes_long_form_footage_to_repurpose_clips():
    body = _text(SKILL_DIR / "body_template.md")
    assert "repurpose-clips" in body
    assert "long-form footage" in body.lower()


def test_body_template_routes_restyle_to_restyle_shorts():
    body = _text(SKILL_DIR / "body_template.md")
    assert "restyle-shorts" in body
    assert "restyled" in body.lower()


def test_body_template_routes_screen_recording_to_demo_clips():
    body = _text(SKILL_DIR / "body_template.md")
    assert "demo-clips" in body
    assert "screen recording" in body.lower()


def test_body_template_does_not_guess_unknown_intent():
    body = _text(SKILL_DIR / "body_template.md")
    assert "Never guess" in body or "do not guess" in body.lower()
    assert "re-ask" in body.lower()


def test_body_template_does_not_call_generation_or_publish_tools():
    body = _text(SKILL_DIR / "body_template.md")
    assert "Do not call any image, video, or publish tool" in body


def test_body_template_is_not_a_graph_node():
    body = _text(SKILL_DIR / "body_template.md")
    assert "does **not** create a graph node" in body


def test_manifest_declares_core_tier():
    from gtm_core.skills.video_router import SKILL

    assert SKILL.capability_tier.value == "core"
    assert SKILL.name == "video-router"


def test_skill_md_exists_and_documents_routing():
    skill_md = _text(SKILL_DIR / "SKILL.md")
    assert "video-router" in skill_md
    assert "short-form-video" in skill_md
    assert "repurpose-clips" in skill_md
    assert "restyle-shorts" in skill_md
    assert "demo-clips" in skill_md
