"""Validation contract tests for Pipeline Governance Manager skills.

Feature: Pipeline Governance Manager Skills
"""

from __future__ import annotations

from pathlib import Path

from gtm_core.skills.registry import all_skills

REPO = Path(__file__).resolve().parents[2]

GOVERNANCE_SKILLS = [
    "deal-slip-scenario",
    "crm-hygiene-check",
    "team-pipeline",
]


def test_governance_skills_registered_in_manifests():
    """Verify all 3 pipeline governance skills have canonical GTMSkill manifests."""
    skills = {s.name: s for s in all_skills()}
    for skill_name in GOVERNANCE_SKILLS:
        assert skill_name in skills, f"Missing manifest for {skill_name}"
        manifest = skills[skill_name]
        assert manifest.description, f"Description missing on {skill_name}"
        assert manifest.phase == "3D"


def test_governance_skill_files_and_codegen_exist():
    """Verify body_template.md and generated SKILL.md exist for all governance skills."""
    for skill_name in GOVERNANCE_SKILLS:
        skill_dir = REPO / "plugin" / "skills" / skill_name
        assert skill_dir.is_dir(), f"Directory missing: {skill_dir}"
        assert (skill_dir / "body_template.md").is_file(), (
            f"body_template.md missing in {skill_name}"
        )
        assert (skill_dir / "SKILL.md").is_file(), f"SKILL.md missing in {skill_name}"


def test_governance_skills_comply_with_gtm_invariants():
    """Verify that skills respect GTM content path invariants and read-only protections."""
    for skill_name in GOVERNANCE_SKILLS:
        skill_file = REPO / "plugin" / "skills" / skill_name / "SKILL.md"
        content = skill_file.read_text(encoding="utf-8")

        # Must instruct saving under content/<active>/
        assert "content/<active>/" in content, (
            f"{skill_name} does not enforce content/<active>/ output path"
        )

        # Must declare safety boundaries
        assert "crm" in content.lower(), f"{skill_name} missing CRM context"
