"""Contract test — the identity-kit skill actually references its load-bearing pieces.

Phase 8 spreads across several files:
the skill's body_template.md / generated SKILL.md, the gtm_core.brandkit write-mode CLI
it shells out to, and the _template BRAND.toml stubs for the two new [identity] keys. This
is the drift guard — if a future edit strips the consent language, the craft guardrails, or
the "never Edit the TOML directly" rule, or if brandkit's CLI surface is renamed without
updating the body, this fails instead of the gap surfacing only at the next live run.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / "plugin" / "skills" / "identity-kit"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_body_template_references_the_brandkit_write_cli():
    body = _text(SKILL_DIR / "body_template.md")
    assert "gtm_core.brandkit" in body
    assert "--set identity." in body
    assert "--create" in body


def test_body_template_carries_the_consent_hard_guardrail():
    body = _text(SKILL_DIR / "body_template.md")
    assert "consent_note" in body
    assert "Refuse to proceed without one" in body
    assert "Article 50" in body


def test_body_template_carries_the_soul_craft_guardrails():
    body = _text(SKILL_DIR / "body_template.md")
    for phrase in (
        "Split the photo set by era",
        "full resolution",
        "One Soul per generation",
        "media_upload_widget",
        "create_voice",
    ):
        assert phrase in body, f"identity-kit body lost its mention of {phrase!r}"


def test_body_template_forbids_direct_toml_edits():
    body = _text(SKILL_DIR / "body_template.md")
    assert "never by editing the TOML" in body
    assert "Only the `gtm_core.brandkit` CLI writes" in body


def test_skill_md_is_in_sync_with_the_consent_gate():
    # SKILL.md is codegen-derived (tests/lint/skill_codegen_sync.sh is the byte-level sync
    # gate); this confirms the consent language actually made it through codegen into what
    # the running skill loads, not only the source template.
    skill_md = _text(SKILL_DIR / "SKILL.md")
    assert "consent_note" in skill_md
    assert "Article 50" in skill_md


def test_brandkit_module_exposes_the_documented_write_surface():
    from gtm_core import brandkit as bk

    assert callable(bk.set_identity_value)
    assert callable(bk.identity_write_target)
    assert "soul_id" in bk._WRITABLE_IDENTITY_KEYS
    assert "voice_id" in bk._WRITABLE_IDENTITY_KEYS
    assert "restyle_preset_id" in bk._WRITABLE_IDENTITY_KEYS
    assert "consent_note" in bk._WRITABLE_IDENTITY_KEYS
    assert "reference_element_ids" in bk._WRITABLE_IDENTITY_KEYS


def test_template_brand_kit_declares_the_two_new_identity_keys():
    template = _text(REPO / "profiles" / "_template" / "knowledge" / "BRAND.toml")
    assert "restyle_preset_id" in template
    assert "consent_note" in template


def test_manifest_is_onboarding_family_and_phase_8():
    from gtm_core.skills.identity_kit import SKILL

    assert SKILL.phase == "8"
    assert SKILL.name == "identity-kit"


def test_skill_is_registered_and_discoverable():
    from gtm_core.skills.registry import all_skills

    assert "identity-kit" in {s.name for s in all_skills()}
