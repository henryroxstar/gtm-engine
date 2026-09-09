"""Every `RULES.md#rN…` link in the repo must resolve to a real §R heading.

Why this exists. Twelve of the quick-reference table's own links were dead for months.
Each heading is `## §RN — Title`; GitHub's slugger drops the `§` and the em-dash but keeps
BOTH surrounding spaces, so the anchor carries a *double* hyphen (`#r13--a-permanent-ban…`).
Every link had been hand-written with one. A broken anchor scrolls nowhere and reports
nothing — the §R11 shape, in a table of contents: it cannot fail, so it never did.

Ground truth is pinned, not derived. `_slug` reimplements github-slugger, and a wrong
reimplementation would agree with itself and pass. `PINNED` holds slugs read back from
GitHub's own `POST /markdown` renderer (2026-09-05); the test asserts the local algorithm
reproduces them before trusting it on the remaining headings. Offline by design — the
network call was the one-time calibration, not the check (§R12).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RULES = REPO / "docs" / "RULES.md"

HEADING = re.compile(r"^## (§R\d+) — (.+)$", re.MULTILINE)
LINK_TO_RULES = re.compile(r"RULES\.md#(r\d+[a-z0-9-]*)")
SELF_LINK = re.compile(r"\]\(#(r\d+[a-z0-9-]*)\)")

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".ruff_cache"}
SCAN_SUFFIXES = {".md", ".py", ".sh", ".yml", ".yaml", ".toml", ".json"}

# Verified against GitHub's own renderer, 2026-09-05. These four cover the punctuation
# that actually varies: the em-dash, a slash, a colon inside backticks, and a hyphenated
# word that must SURVIVE (hyphen is not in the strip set).
PINNED = {
    "§R6": "r6--all-external-io-via-mcp",
    "§R7": "r7--autopublish-false-always",
    "§R9": "r9--no-third-party-pii-outside-profiles-and-content",
    "§R13": "r13--a-permanent-ban-lives-in-code-not-prose",
}

# github-slugger: lowercase, strip punctuation, THEN spaces -> hyphens. It does not
# collapse runs, which is the whole reason " — " yields two hyphens.
_STRIP = re.compile(r"[\0-\x1f!-,./:-@\[-^`{-\xa0\xa7‐-‧‰-⁞]")


def _slug(heading_text: str) -> str:
    return _STRIP.sub("", heading_text.lower().strip()).replace(" ", "-")


def _headings() -> dict[str, str]:
    return dict(HEADING.findall(RULES.read_text()))


def _scan_files():
    for path in sorted(REPO.rglob("*")):
        if path.suffix not in SCAN_SUFFIXES or not path.is_file():
            continue
        if SKIP_DIRS.intersection(path.parts):
            continue
        yield path


def test_slugger_reproduces_github_ground_truth():
    """The local algorithm must match what GitHub actually emitted, or it proves nothing."""
    headings = _headings()
    for num, expected in PINNED.items():
        assert num in headings, f"{num} heading missing from RULES.md"
        assert _slug(f"{num} — {headings[num]}") == expected, (
            f"{num}: local slugger disagrees with GitHub's renderer — fix _slug, "
            f"do not edit PINNED without re-running POST /markdown"
        )


def test_slugger_rejects_the_single_hyphen_form():
    """Negative control: the shape that was wrong for months must not be produced."""
    slug = _slug("§R13 — A permanent ban lives in code, not prose")
    assert "--" in slug, "an em-dash heading must yield a double hyphen"
    assert slug != "r13-a-permanent-ban-lives-in-code-not-prose"


def test_every_rules_anchor_in_the_repo_resolves():
    valid = {_slug(f"{num} — {title}") for num, title in _headings().items()}
    assert len(valid) >= 13, f"expected 13+ rule headings, found {len(valid)}"

    broken, checked = [], 0
    for path in _scan_files():
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        anchors = LINK_TO_RULES.findall(text)
        if path == RULES:
            anchors += SELF_LINK.findall(text)
        for anchor in anchors:
            checked += 1
            if anchor not in valid:
                broken.append(f"{path.relative_to(REPO)}: #{anchor}")

    assert checked >= 13, (
        f"only {checked} anchors found — the scan is not reaching the docs, "
        "so a clean result here would mean nothing"
    )
    assert not broken, "anchors pointing at no heading:\n  " + "\n  ".join(broken)


def test_quick_reference_covers_every_rule():
    """The table is the index; a rule missing from it is invisible. §R9 was, for months."""
    text = RULES.read_text()
    table = text[text.index("## Quick reference") : text.index("## §R1 ")]
    missing = [num for num in _headings() if f"[{num}](" not in table]
    assert not missing, f"rules with no quick-reference row: {missing}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
