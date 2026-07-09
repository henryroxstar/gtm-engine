# tests/journey/test_gitscan.py
"""Unit tests for gtm_core.journey.gitscan (Task 1)."""

import subprocess
from pathlib import Path

import pytest

from gtm_core.journey.gitscan import (
    _ALLOWED,
    Commit,
    _parse_stat_line,
    _run,
    clusters_window,
    commits,
    first_sha,
    head_sha,
    show,
)

REPO = Path(__file__).resolve().parents[2]


# ── allowlist enforcement ────────────────────────────────────────────────────


def test_allowlist_blocks_push():
    with pytest.raises(ValueError, match="not in allowlist"):
        _run(["push"])


def test_allowlist_blocks_pull():
    with pytest.raises(ValueError, match="not in allowlist"):
        _run(["pull"])


def test_allowlist_blocks_fetch():
    with pytest.raises(ValueError, match="not in allowlist"):
        _run(["fetch"])


def test_allowlist_blocks_reset():
    with pytest.raises(ValueError, match="not in allowlist"):
        _run(["reset", "--hard", "HEAD"])


def test_allowlist_blocks_empty():
    with pytest.raises(ValueError, match="not in allowlist"):
        _run([])


def test_allowed_commands_are_readonly():
    assert _ALLOWED == frozenset({"log", "show", "diff", "rev-list", "shortlog"})


# ── stat parsing ─────────────────────────────────────────────────────────────


def test_parse_stat_line_full():
    line = "3 files changed, 45 insertions(+), 12 deletions(-)"
    f, i, d = _parse_stat_line(line)
    assert f == 3 and i == 45 and d == 12


def test_parse_stat_line_insertions_only():
    line = "1 file changed, 10 insertions(+)"
    f, i, d = _parse_stat_line(line)
    assert f == 1 and i == 10 and d == 0


def test_parse_stat_line_empty():
    f, i, d = _parse_stat_line("")
    assert f == 0 and i == 0 and d == 0


# ── real repo reads (integration, requires actual git history) ───────────────


def test_head_sha_is_40_chars():
    sha = head_sha(repo_root=REPO)
    assert len(sha) == 40
    assert all(c in "0123456789abcdef" for c in sha)


def test_first_sha_is_40_chars():
    sha = first_sha(repo_root=REPO)
    assert len(sha) == 40


def test_first_sha_is_oldest():
    """first_sha + HEAD..first_sha should yield no commits (it IS the bottom)."""
    f = first_sha(repo_root=REPO)
    # Rev-list from first_sha^..first_sha should be exactly one commit
    result = subprocess.run(
        ["git", "-C", str(REPO), "rev-list", f"{f}^..{f}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Shallow repo — no parent for the first commit
        pytest.skip("shallow repo: first commit has no parent")
    assert f in result.stdout


def test_commits_returns_list_of_commit_objects():
    result = commits(repo_root=REPO)
    assert isinstance(result, list)
    assert len(result) > 0
    c = result[0]
    assert isinstance(c, Commit)
    assert len(c.sha) == 40
    assert c.date  # non-empty ISO date
    assert c.subject  # non-empty


def test_commits_since_sha_is_exclusive():
    """commits(since_sha=HEAD) should return empty (no commits after HEAD)."""
    h = head_sha(repo_root=REPO)
    result = commits(since_sha=h, repo_root=REPO)
    assert result == []


def test_clusters_window_returns_list_of_dicts():
    result = clusters_window(repo_root=REPO)
    assert isinstance(result, list)
    assert len(result) > 0
    first = result[0]
    assert "theme" in first
    assert "date" in first
    assert "commits" in first
    assert 0.0 <= first["score_hint"] <= 1.0


def test_clusters_sorted_newest_first():
    result = clusters_window(repo_root=REPO)
    dates = [c["date"] for c in result]
    assert dates == sorted(dates, reverse=True)


def test_show_returns_string():
    h = head_sha(repo_root=REPO)
    result = show(sha=h, repo_root=REPO)
    assert isinstance(result, str)
    assert h[:7] in result or "commit" in result.lower()
