"""Contract test — content-publish actually documents the Article 50 disclosure
check and the ⟦IDENTITY⟧ marker shape (Phase 12, §6.2).

The gap this closes: the disclosure check has two independent enforcement points —
the skill's own Step 1b (refuses to stage) and the cockpit's re-verification at
staging (cockpit/gates.py, tested in tests/cockpit/test_disclosure_gate.py). This is
the drift guard for the skill-side half plus the marker shape both sides agree on.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / "plugin" / "skills" / "content-publish"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_body_template_has_a_disclosure_step():
    body = _text(SKILL_DIR / "body_template.md")
    assert "Step 1b" in body
    assert "identity_used" in body
    assert "Art. 50" in body or "Article 50" in body


def test_body_template_refuses_to_proceed_without_disclosure():
    body = _text(SKILL_DIR / "body_template.md")
    assert "do not proceed to Step 2" in body
    assert "disclosure.line" in body


def test_body_template_documents_the_identity_marker_shape():
    body = _text(SKILL_DIR / "body_template.md")
    assert "⟦IDENTITY⟧soul,voice⟦/IDENTITY⟧" in body
    assert "optional" in body.lower()


def test_body_template_omits_the_marker_when_nothing_synthetic():
    body = _text(SKILL_DIR / "body_template.md")
    assert "omit `⟦IDENTITY⟧" in body or "omit ⟦IDENTITY⟧" in body


def test_skill_md_in_sync_with_the_disclosure_step():
    skill_md = _text(SKILL_DIR / "SKILL.md")
    assert "identity_used" in skill_md
    assert "Article 50" in skill_md


def test_manifest_bumped_past_0_1_0_for_disclosure():
    from gtm_core.skills.content_publish import SKILL

    assert SKILL.version != "0.1.0"


def test_agent_publish_exposes_the_disclosure_validator():
    from agent.publish import parse_publish_block, validate_disclosure

    assert callable(validate_disclosure)
    draft = parse_publish_block("⟦GATE:publish⟧\n⟦POST⟧\nhi\n⟦/POST⟧\n⟦IDENTITY⟧soul⟦/IDENTITY⟧")
    assert draft is not None
    assert draft.identity_used == ("soul",)
