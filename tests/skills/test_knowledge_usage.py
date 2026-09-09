"""Tests for gtm_core.knowledge_usage (knowledge-lifecycle PRD, Phase 2).

The final test is the CI drift enforcer — the committed docs/knowledge-usage.md must match a fresh
render — mirroring tests/skills/test_codegen.py::test_committed_skill_md_in_sync.
"""

from __future__ import annotations

from gtm_core import knowledge_usage as ku

# --- reference extraction -----------------------------------------------------


def test_normalize_strips_ext_and_rejects_placeholders():
    assert ku._normalize("icp-personas.md") == "icp-personas"
    assert ku._normalize("voice-bans") == "voice-bans"
    assert ku._normalize("guidance/01-nist.md") == "guidance/01-nist"
    assert ku._normalize("<file>") is None
    assert ku._normalize("--profile") is None


def test_topics_in_text_all_three_forms_plus_dir():
    text = (
        "reads knowledge/company.md and profiles/<active>/knowledge/icp-personas.md; "
        "run resolve_knowledge voice-bans; load knowledge/guidance/ every file; "
        "assets under knowledge/brand/ are ignored"
    )
    topics = ku._topics_in_text(text)
    assert {"company", "icp-personas", "voice-bans", "guidance/*"} <= topics
    assert "brand/*" not in topics  # brand/ is excluded (assets, not a topic)


# --- scan + invert on a synthetic skills tree ---------------------------------


def _mk(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_scan_and_invert(tmp_path):
    skills = tmp_path / "plugin" / "skills"
    _mk(skills / "alpha" / "SKILL.md", "reads knowledge/company.md, resolve_knowledge voice")
    _mk(skills / "beta" / "body_template.md", "read knowledge/guidance/ every file")
    _mk(skills / "gamma" / "SKILL.md", "no knowledge here")

    scan = ku.scan_skills(skills)
    assert scan["alpha"] == {"company", "voice"}
    assert scan["beta"] == {"guidance/*"}
    assert scan["gamma"] == set()

    rev = ku.topic_to_skills(scan)
    assert rev["company"] == ["alpha"]
    assert rev["guidance/*"] == ["beta"]


# --- coverage: orphans, unprovided, directory crediting -----------------------


def test_coverage_orphans_unprovided_and_dir_crediting(tmp_path):
    skills = tmp_path / "plugin" / "skills"
    _mk(skills / "alpha" / "SKILL.md", "knowledge/company.md; resolve_knowledge voice")
    _mk(skills / "beta" / "SKILL.md", "read knowledge/guidance/ every file")

    prof = tmp_path / "profiles" / "acme"
    _mk(prof / "PROFILE.md", "name: Acme\n")
    _mk(prof / "knowledge" / "company.md", "# Company\n")  # consumed (exact)
    _mk(prof / "knowledge" / "guidance" / "01-x.md", "# G\n")  # consumed (via guidance/ dir ref)
    _mk(prof / "knowledge" / "orphan-topic.md", "# Orphan\n")  # nobody reads it

    cov = ku.coverage(tmp_path / "profiles", "acme", repo_root=tmp_path)
    assert cov["orphans"] == ["orphan-topic"]  # guidance/01-x credited via the dir ref
    assert cov["unprovided"] == ["voice"]  # a skill reads 'voice'; acme has no voice file


# --- CI drift enforcer --------------------------------------------------------


def test_committed_usage_doc_in_sync():
    """docs/knowledge-usage.md must match a fresh render.
    Regenerate with: uv run python -m gtm_core.knowledge_usage generate"""
    assert ku.check(), (
        "docs/knowledge-usage.md is stale — run: uv run python -m gtm_core.knowledge_usage generate"
    )
