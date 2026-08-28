"""Contract tests for the video-router skill.

The router asks one plain-English question and dispatches to the correct creator pack
variant. It is not a graph node and spends no credits.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / "plugin" / "skills" / "video-router"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_body_template_asks_the_one_question():
    body = _text(SKILL_DIR / "body_template.md")
    # Reworded 2026-08-20 (PRD W7.1): footage is asked about FIRST. It is the primary lane now —
    # better output, lower cost, no Art. 50 disclosure — and burying it made the worse lane the
    # default. Both halves must still be offered.
    assert "do you have footage already" in body.lower()
    assert "write something from scratch" in body.lower()


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
