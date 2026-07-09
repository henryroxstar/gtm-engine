# gtm_core/journey/gitscan.py
"""Read-only git scanner for the journey radar.

Invoked as `python -m gtm_core.journey.gitscan <subcommand> [args]` from skill
body_templates. Never called via raw Bash git — keeps agent/permissions.py unchanged (§R8).

Allowed subcommands: log, show, diff, rev-list, shortlog
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

_ALLOWED = frozenset({"log", "show", "diff", "rev-list", "shortlog"})

_CC_RE = re.compile(
    r"^(feat|fix|refactor|docs|style|test|chore|perf)(?:\(([^)]+)\))?:", re.IGNORECASE
)
_STAT_RE = re.compile(r"(\d+) file|(\d+) insertion|(\d+) deletion")

# Unit separator — never appears in commit messages
_SEP = "\x1f"
_LOG_FMT = f"%H{_SEP}%aI{_SEP}%s{_SEP}%b{_SEP}%x00"


def _repo_root() -> Path:
    """Repo root: GTM_REPO_ROOT env override, else derive from this file's location."""
    override = os.getenv("GTM_REPO_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    # gtm_core/journey/gitscan.py -> parents[2] = repo root
    return Path(__file__).resolve().parents[2]


def _run(args: list[str], repo_root: Path | None = None) -> str:
    """Run an allowlisted git subcommand. Raises ValueError for any other subcommand."""
    subcmd = args[0] if args else ""
    if subcmd not in _ALLOWED:
        raise ValueError(
            f"git subcommand not in allowlist: {subcmd!r}. Allowed: {sorted(_ALLOWED)}"
        )
    root = repo_root or _repo_root()
    result = subprocess.run(
        ["git", "-C", str(root)] + args,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return result.stdout


@dataclass(frozen=True)
class Commit:
    sha: str
    date: str  # ISO-8601
    subject: str
    body: str
    files_changed: int
    insertions: int
    deletions: int


def _parse_stat_line(line: str) -> tuple[int, int, int]:
    """Parse a --shortstat line → (files, insertions, deletions)."""
    files = insertions = deletions = 0
    for m in _STAT_RE.finditer(line):
        g1, g2, g3 = m.group(1), m.group(2), m.group(3)
        if g1:
            files = int(g1)
        elif g2:
            insertions = int(g2)
        elif g3:
            deletions = int(g3)
    return files, insertions, deletions


def commits(
    since_sha: str | None = None,
    until: str = "HEAD",
    repo_root: Path | None = None,
) -> list[Commit]:
    """Return commits (newest first). `since_sha` is exclusive (i.e. `since_sha..until`)."""
    rev_range = f"{since_sha}..{until}" if since_sha else until
    raw = _run(["log", f"--format={_LOG_FMT}", "--shortstat", rev_range], repo_root)
    result: list[Commit] = []
    # --shortstat appends the stat line AFTER the %x00 of the *current* commit,
    # so it appears at the START of the next NUL-delimited block. We split on NUL
    # and peel the trailing stat (which belongs to the previous commit) from the
    # beginning of each subsequent block before parsing.
    blocks = raw.split("\x00")
    pending: list[tuple[str, str, str, str, str]] = []  # (sha, date, subject, body, stat_line)
    for raw_block in blocks:
        # Leading content before the first SEP is either:
        #   • the stat line from the previous commit (when it starts with whitespace / digits)
        #   • or the SHA of a new commit (40 hex chars)
        # We partition: everything up to the first SEP is consumed as the
        # "leading chunk" — which may start with a stat line followed by a SHA.
        stripped = raw_block.lstrip("\n")
        if not stripped:
            continue
        # Extract optional leading stat line (starts with whitespace, contains "changed")
        stat_line = ""
        remainder = stripped
        lines = stripped.splitlines()
        stat_candidates = [ln for ln in lines if "changed" in ln]
        if stat_candidates:
            # The stat line precedes the next commit SHA on its own line(s)
            stat_line = stat_candidates[0]
            # Remove the stat line(s) from the remainder
            stat_idx = stripped.find(stat_line)
            remainder = stripped[stat_idx + len(stat_line) :].lstrip("\n")

        # Attach accumulated stat to the most-recently parsed commit
        if stat_line and pending:
            last = pending[-1]
            pending[-1] = (last[0], last[1], last[2], last[3], stat_line)

        if not remainder.strip():
            continue

        parts = remainder.split(_SEP, 4)
        if len(parts) < 3:
            continue
        sha = parts[0].strip()
        date = parts[1].strip()
        subject = parts[2].strip()
        body = parts[3].strip() if len(parts) > 3 else ""
        pending.append((sha, date, subject, body, ""))

    for entry in pending:
        sha, date, subject, body, stat_line = entry
        files, insertions, deletions = _parse_stat_line(stat_line) if stat_line else (0, 0, 0)
        result.append(
            Commit(
                sha=sha,
                date=date,
                subject=subject,
                body=body,
                files_changed=files,
                insertions=insertions,
                deletions=deletions,
            )
        )
    return result


def clusters_window(
    since_sha: str | None = None,
    repo_root: Path | None = None,
) -> list[dict]:
    """Group commits into narrative clusters by conventional-commit type+scope + day.

    Returns list of dicts sorted newest date first:
      {"theme": str, "date": str, "commits": [dict], "score_hint": float}
    """
    all_commits = commits(since_sha=since_sha, repo_root=repo_root)
    groups: dict[tuple[str, str, str], list[Commit]] = defaultdict(list)
    for c in all_commits:
        m = _CC_RE.match(c.subject)
        ctype = m.group(1).lower() if m else "misc"
        scope = (m.group(2) or "").lower() if m else ""
        day = c.date[:10]
        groups[(ctype, scope, day)].append(c)

    _BASE = {"feat": 0.80, "fix": 0.60, "refactor": 0.50, "docs": 0.40, "misc": 0.25}
    result = []
    for (ctype, scope, day), cms in groups.items():
        theme = f"{ctype}({scope})" if scope else ctype
        total_lines = sum(c.insertions + c.deletions for c in cms)
        hint = min(
            1.0, _BASE.get(ctype, 0.25) + 0.05 * len(cms) + 0.10 * min(1.0, total_lines / 300)
        )
        result.append(
            {
                "theme": theme,
                "date": day,
                "commits": [asdict(c) for c in cms],
                "score_hint": round(hint, 3),
            }
        )
    return sorted(result, key=lambda x: x["date"], reverse=True)


def show(sha: str, path: str | None = None, repo_root: Path | None = None) -> str:
    """Show a commit (diff) or file at a commit."""
    args = ["show", "--stat", sha]
    if path:
        args += ["--", path]
    return _run(args, repo_root)


def head_sha(repo_root: Path | None = None) -> str:
    """Return current HEAD SHA."""
    return _run(["rev-list", "-1", "HEAD"], repo_root).strip()


def first_sha(repo_root: Path | None = None) -> str:
    """Return the oldest commit SHA in the repo."""
    raw = _run(["rev-list", "--reverse", "HEAD"], repo_root)
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    return lines[0] if lines else ""


def _cli() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Read-only git scanner (allowlisted subcommands only)")
    sub = p.add_subparsers(dest="cmd", required=True)

    lg = sub.add_parser("log", help="List commits as JSON")
    lg.add_argument("--since-sha", default=None)
    lg.add_argument("--until", default="HEAD")

    cl = sub.add_parser("clusters", help="Cluster commits by theme as JSON")
    cl.add_argument("--since-sha", default=None)

    sh = sub.add_parser("show", help="Show a commit")
    sh.add_argument("sha")
    sh.add_argument("--path", default=None)

    sub.add_parser("head-sha", help="Print HEAD SHA")
    sub.add_parser("first-sha", help="Print oldest commit SHA")

    args = p.parse_args()
    if args.cmd == "log":
        print(
            json.dumps(
                [asdict(c) for c in commits(since_sha=args.since_sha, until=args.until)], indent=2
            )
        )
    elif args.cmd == "clusters":
        print(json.dumps(clusters_window(since_sha=args.since_sha), indent=2))
    elif args.cmd == "show":
        print(show(sha=args.sha, path=args.path))
    elif args.cmd == "head-sha":
        print(head_sha())
    elif args.cmd == "first-sha":
        print(first_sha())


if __name__ == "__main__":
    _cli()
