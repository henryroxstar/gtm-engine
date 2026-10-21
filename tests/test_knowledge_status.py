"""Tests for gtm_core.knowledge_status — the unified corpus dashboard (PRD Phase 5).

Uses a synthetic profile + a synthetic skills tree (injected via skills_root) so the dashboard is
hermetic — it does not depend on the real plugin/skills corpus.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

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


# --- T13: the CLI path a scheduled unit actually execs ------------------------
#
# Both tests above call `status()` as a library function. `gtm-knowledge-status.service`
# execs `python -m gtm_core.knowledge_status report --profile <p>`, and nothing covered
# that path — so the daily unit added in W3 (PRD 2026-08-27 §3.7) would have been the
# first thing to run it. A unit is a poor place to discover an argparse defect.
#
# What it must surface is the staged-but-unpromoted queue: the knowledge lifecycle ships
# staging and promotion, and candidates pile up silently because nothing says they are
# there. That is the same shape as an unscheduled gate.


class TestTheReportCli:
    def _args(self, tmp_path, *extra: str) -> list[str]:
        profiles_root, content_root, _ = _fixture(tmp_path)
        return [
            "report",
            "--profile",
            "acme",
            "--profiles-root",
            str(profiles_root),
            "--content-root",
            str(content_root),
            *extra,
        ]

    def test_report_exits_zero(self, tmp_path, capsys):
        assert kstat.main(self._args(tmp_path)) == 0
        assert capsys.readouterr().out.strip(), "report printed nothing"

    def test_json_mode_is_parseable_and_carries_the_rows(self, tmp_path, capsys):
        assert kstat.main(self._args(tmp_path, "--json")) == 0
        payload = json.loads(capsys.readouterr().out)
        assert len(payload) == 1
        assert {r["topic"] for r in payload[0]["rows"]} == {"company", "stale", "orphan"}

    def test_the_staged_but_unpromoted_queue_is_surfaced(self, tmp_path, capsys):
        """The gap this unit exists to close — a staged candidate nobody is told about."""
        assert kstat.main(self._args(tmp_path, "--json")) == 0
        payload = json.loads(capsys.readouterr().out)
        staged = [r["topic"] for r in payload[0]["rows"] if r["staged"]]
        assert staged == ["company"]
        assert payload[0]["summary"]["staged"] == 1

    def test_the_human_report_names_the_overdue_topic(self, tmp_path, capsys):
        kstat.main(self._args(tmp_path))
        assert "stale" in capsys.readouterr().out

    def test_no_profile_and_no_all_is_a_hard_stop(self, tmp_path):
        """Silently reporting nothing would read as a clean corpus."""
        with pytest.raises(SystemExit):
            kstat.main(["report"])
