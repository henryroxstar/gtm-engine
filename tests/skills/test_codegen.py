"""Phase A codegen guards: the manifest is canonical, SKILL.md is generated, and
the prompt body is never mangled by migration. These run in the `tests` CI job
(pytest) and back the tests/lint/skill_codegen_sync.sh local gate.
"""

from pathlib import Path

from gtm_core.skills import codegen, registry

PLUGIN = Path(__file__).resolve().parents[2] / "plugin"


def test_registry_nonempty_and_every_skill_has_a_tier():
    skills = registry.all_skills()
    assert skills, "skill registry is empty"
    for skill in skills:
        assert skill.capability_tier is not None, f"{skill.name} has no capability_tier"


def test_committed_skill_md_in_sync():
    drift = codegen.check(PLUGIN)
    assert drift == [], (
        "committed SKILL.md drifted from its manifest — run: "
        f"python -m gtm_core.skills.codegen generate-all (stale: {drift})"
    )


def test_check_reports_absent_generated_files_as_drift(tmp_path):
    """A manifest with no generated SKILL.md yet must come back as drift, not raise.
    Blind `.read_text()` turned the "new skill, codegen not run" case into a
    FileNotFoundError traceback that hid the gate's actionable fix message.
    """
    empty_plugin_root = tmp_path / "plugin"
    drift = codegen.check(empty_plugin_root)
    assert {d.split(" ")[0] for d in drift} >= {s.name for s in registry.all_skills()}
    assert "docs/SKILLS.md" in drift


def test_prompt_body_passes_through_verbatim():
    for skill in registry.all_skills():
        body_file = PLUGIN / "skills" / skill.name / "body_template.md"
        if not body_file.exists():
            continue  # private-distribution stub (OSS carve paid-tier stub) — no source to check
        body = body_file.read_text(encoding="utf-8")
        generated = (PLUGIN / "skills" / skill.name / "SKILL.md").read_text(encoding="utf-8")
        assert body.rstrip("\n") in generated, f"{skill.name}: prompt body not preserved verbatim"
        assert generated.endswith(codegen.OPERATOR_CLOSE_BLOCK), (
            f"{skill.name}: missing operator close block"
        )


def test_say_phrases():
    prospect_skill = next(s for s in registry.all_skills() if s.name == "prospect")
    phrases = codegen.say_phrases(prospect_skill.description)
    assert phrases == (
        "prospect for accounts",
        "find buyers at [company]",
        "build prospect list for [industry]",
        "qualify leads",
        "run prospecting sweep",
    )
    assert codegen.say_phrases("A description with no trigger clause") == ()
