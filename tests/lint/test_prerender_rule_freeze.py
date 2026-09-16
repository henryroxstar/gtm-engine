"""Pre-render rule freeze ratchet (Q1) — the pre-render text lint ceiling.

Freezes the combined line counts of pre-render text linting packages (`gtm_core/shots_lint`
and `gtm_core/content_quality`) to prevent endless gaming of text rules while synthetic videos
remain unconvincing on screen.

Engineering effort must focus on post-render measurement (Q2–Q8: vision pixel gate, blinded
human panels, unprompted audience reception), not adding more pre-render text rules.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: The standing ceiling for pre-render text lint line counts.
#: Non-increasing. Decreases are welcome (ratchet down).
MAX_PRERENDER_LINES = 4667

PRE_RENDER_PACKAGES = (
    "gtm_core/shots_lint",
    "gtm_core/content_quality",
)


def count_package_lines(pkg_rel_path: str) -> int:
    """Count all Python source lines in a package directory."""
    pkg_dir = REPO / pkg_rel_path
    if not pkg_dir.is_dir():
        return 0
    total = 0
    for root, _, files in os.walk(pkg_dir):
        for f in files:
            if f.endswith(".py"):
                p = os.path.join(root, f)
                with open(p, encoding="utf-8") as fh:
                    total += sum(1 for _ in fh)
    return total


def test_prerender_rules_do_not_expand() -> None:
    """Assert combined lines of pre-render linter packages do not exceed the Q1 freeze."""
    counts = {pkg: count_package_lines(pkg) for pkg in PRE_RENDER_PACKAGES}
    actual_lines = sum(counts.values())

    breakdown = ", ".join(f"{pkg}: {c}" for pkg, c in counts.items())
    assert actual_lines <= MAX_PRERENDER_LINES, (
        f"Pre-render rule freeze (Q1) violated: {actual_lines} > {MAX_PRERENDER_LINES} ({breakdown}). "
        "Engineering effort must shift to post-render measurement (Q2-Q8), not expanding pre-render text rules. "
        "If removing dead rules or refactoring, lower MAX_PRERENDER_LINES to lock in the reduction."
    )
