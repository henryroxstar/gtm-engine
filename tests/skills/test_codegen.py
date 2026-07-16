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


def test_prompt_body_passes_through_verbatim():
    for skill in registry.all_skills():
        if skill.fallback_note:
            continue  # body is followed by an appended degraded-mode section
        body = (PLUGIN / "skills" / skill.name / "body_template.md").read_text(encoding="utf-8")
        generated = (PLUGIN / "skills" / skill.name / "SKILL.md").read_text(encoding="utf-8")
        assert generated.endswith(body), f"{skill.name}: prompt body not preserved verbatim"
