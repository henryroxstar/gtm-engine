"""Tests for gtm_core.knowledge_status — the unified corpus dashboard (PRD Phase 5).

Uses a synthetic profile + a synthetic skills tree (injected via skills_root) so the dashboard is
hermetic — it does not depend on the real plugin/skills corpus.
"""

from __future__ import annotations

from datetime import date, timedelta

from gtm_core import knowledge_status as kstat

TODAY = date(2026, 6, 1)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fm(owner: str | None, refreshed: str, review: str) -> str:
    owner_line = f"owner: {owner}\n" if owner else ""
    return f"---\nsource: manual\n{owner_line}refreshed: {refreshed}\nreview: {review}\n---\n# T\nbody\n"


def _fixture(tmp_path):
    profiles_root = tmp_path / "profiles"
    content_root = tmp_path / "content"
    skills_root = tmp_path / "plugin" / "skills"

    _write(profiles_root / "acme" / "PROFILE.md", "name: Acme\n")
    kdir = profiles_root / "acme" / "knowledge"
    _write(
        kdir / "company.md", _fm("alice", (TODAY - timedelta(days=10)).isoformat(), "90d")
    )  # owned, fresh, read
    _write(
        kdir / "stale.md", _fm("bob", (TODAY - timedelta(days=200)).isoformat(), "90d")
    )  # owned, overdue, read? no
    _write(
        kdir / "orphan.md", _fm(None, (TODAY - timedelta(days=1)).isoformat(), "90d")
    )  # unowned, fresh, unread

    # a skill that reads only 'company'
    _write(skills_root / "alpha" / "SKILL.md", "reads knowledge/company.md")

    # a staged refresh candidate for company
    _write(content_root / "acme" / "knowledge-staging" / "company.md", "staged candidate")

    return profiles_root, content_root, skills_root


def test_status_rows_and_counts(tmp_path):
    profiles_root, content_root, skills_root = _fixture(tmp_path)
    d = kstat.status(profiles_root, content_root, "acme", TODAY, skills_root=skills_root)

    by_topic = {r["topic"]: r for r in d["rows"]}
    assert by_topic["company"]["owner"] == "alice"
    assert by_topic["company"]["consumers"] == ["alpha"]
    assert by_topic["company"]["orphan"] is False
    assert by_topic["company"]["staged"] is True
    assert by_topic["stale"]["status"] == "overdue"
    assert by_topic["orphan"]["owner"] is None
    assert by_topic["orphan"]["orphan"] is True

    s = d["summary"]
    assert s["managed"] == 3
    assert s["due"] == 1  # stale
    assert s["unowned"] == 1  # orphan.md
    assert s["orphan"] == 2  # stale.md + orphan.md are read by no skill (only company is)
    assert s["staged"] == 1  # company staged

    assert d["owners"] == {"alice": 1, "bob": 1, "(unowned)": 1}


def test_directory_reference_credits_consumers(tmp_path):
    profiles_root = tmp_path / "profiles"
    content_root = tmp_path / "content"
    skills_root = tmp_path / "plugin" / "skills"
    _write(profiles_root / "acme" / "PROFILE.md", "name: Acme\n")
    _write(
        profiles_root / "acme" / "knowledge" / "guidance" / "01-x.md",
        _fm("alice", TODAY.isoformat(), "180d"),
    )
    _write(skills_root / "beta" / "SKILL.md", "read knowledge/guidance/ every file")

    d = kstat.status(profiles_root, content_root, "acme", TODAY, skills_root=skills_root)
    row = d["rows"][0]
    assert row["topic"] == "guidance/01-x"
    assert row["consumers"] == ["beta"]  # credited via the guidance/ directory reference
    assert row["orphan"] is False
