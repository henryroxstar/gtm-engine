"""Lint test ensuring content/ directory is gitignored and carries no tracked files.

Enforces §R9 (No third-party PII outside profiles/ and content/) and egress safety:
ensures content/ is never accidentally tracked or committed to git.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def test_content_dir_is_gitignored() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    gitignore_path = repo_root / ".gitignore"

    assert gitignore_path.exists(), ".gitignore must exist in repo root"
    content = gitignore_path.read_text(encoding="utf-8")

    # Verify content/ or content is present in .gitignore
    lines = [line.strip() for line in content.splitlines()]
    has_content_rule = any(
        line in ("content/", "content", "/content/", "/content") or line.startswith("content/")
        for line in lines
        if not line.startswith("#")
    )
    assert has_content_rule, ".gitignore must contain an entry ignoring content/"


def test_no_content_files_tracked_in_git() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    # Run git ls-files content/
    proc = subprocess.run(
        ["git", "ls-files", "content/"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    tracked_files = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    assert len(tracked_files) == 0, (
        f"Found files in content/ tracked by git (PII egress violation): {tracked_files}"
    )
