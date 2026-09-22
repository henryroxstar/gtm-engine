"""Validation contract tests for Product & Marketing playbooks.

Feature: Product & Marketing Playbooks
"""

from __future__ import annotations

from pathlib import Path

from agent.permissions import classify_tool, email_context
from gtm_core.skills.registry import all_skills

REPO = Path(__file__).resolve().parents[2]

PRODUCT_MARKETING_SKILLS = [
    "synthesize-research",
    "metrics-review",
]


def test_product_marketing_skills_registered_in_manifests():
    """Verify product management skills have canonical GTMSkill manifests."""
    skills = {s.name: s for s in all_skills()}
    for skill_name in PRODUCT_MARKETING_SKILLS:
        assert skill_name in skills, f"Missing manifest for {skill_name}"
        manifest = skills[skill_name]
        assert manifest.description, f"Description missing on {skill_name}"
        assert manifest.phase == "3D"


def test_product_marketing_skill_files_and_codegen_exist():
    """Verify body_template.md and generated SKILL.md exist for product skills."""
    for skill_name in PRODUCT_MARKETING_SKILLS:
        skill_dir = REPO / "plugin" / "skills" / skill_name
        assert skill_dir.is_dir(), f"Directory missing: {skill_dir}"
        assert (skill_dir / "body_template.md").is_file(), (
            f"body_template.md missing in {skill_name}"
        )
        assert (skill_dir / "SKILL.md").is_file(), f"SKILL.md missing in {skill_name}"


def test_product_skills_comply_with_gtm_invariants():
    """Verify that skills respect GTM content path invariants and untrusted data warnings."""
    for skill_name in PRODUCT_MARKETING_SKILLS:
        skill_file = REPO / "plugin" / "skills" / skill_name / "SKILL.md"
        content = skill_file.read_text(encoding="utf-8")
        assert "content/<active>/" in content, (
            f"{skill_name} does not enforce content/<active>/ output path"
        )


def test_marketing_automation_structural_gate(monkeypatch):
    """PRD §2.1 & Test Plan §3.B: Verify that blast/send verbs on ~~marketing_automation are denied."""
    monkeypatch.setattr(
        "gtm_core.mcp_categories.get_category_for_connector",
        lambda name, config=None: "~~marketing_automation" if name == "customerio" else None,
    )

    # Denied outside email_context
    assert classify_tool("mcp__customerio__send_blast", {}) == "deny"
    assert classify_tool("mcp__customerio__activate_campaign", {}) == "deny"
    assert classify_tool("mcp__customerio__launch_drip", {}) == "deny"

    # Staging/reading is allowed
    assert classify_tool("mcp__customerio__get_campaigns", {}) == "allow"
    assert classify_tool("mcp__customerio__preview_draft", {}) == "allow"

    # Allowed inside approved context
    with email_context():
        assert classify_tool("mcp__customerio__send_blast", {}) == "allow"
