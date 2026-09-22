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
#: Non-increasing except for a dated, justified raise (never a rule addition — see below).
#: Decreases are welcome (ratchet down).
#: 2026-09-22 +28 (4667->4695): gtm_core/content_quality/{post,model}.py each gained a
#: try/except around their gtm_core.video_lint import. This is NOT a new pre-render text
#: rule — it's a degradation guard so content-plan/content-publish/content-studio (whose
#: SKILL.md-cited commands don't touch video) keep working where the video tier is absent
#: (the OSS carve withholds gtm_core.video_lint entirely); _video_post_check refuses
#: outright rather than reporting a hollow pass when the guard trips (§R18).
MAX_PRERENDER_LINES = 4695

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
