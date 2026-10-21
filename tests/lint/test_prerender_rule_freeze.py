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
#: 2026-09-23 +3 (4695->4698): gtm_core/content_quality/sources.py's `load_hook_matrix`
#: gained an `overlay` parameter so a messaging experiment can resolve an alternate
#: hook-matrix.md (P1). This is NOT a new pre-render text rule — no judgement, no threshold
#: and no finding is added; it is one more argument on an existing path resolver, and the
#: three lines are the reformatted signature. The rule this freeze protects is unchanged:
#: effort goes to post-render measurement, not to more pre-render prose checks.
#: 2026-10-16 +119 (4698->4817): gtm_core/shots_lint/screen_ui.py validates production.screen_ui
#: shape, backend ("pillow" | "html"), mutual exclusivity (scene vs composition), and string copy ceilings
#: for HTML motion scenes (PRD 2026-10-16-html-motion-scenes). Not a text-prompt rule — structural schema
#: validation for deterministic screen UI shots.
#: 2026-10-17 +11 (4817->4828): gtm_core/shots_lint/{api,camera}.py gained try/except fallback imports
#: around gtm_core.storyboard. Not a new pre-render text rule — degradation guard so shots_lint and
#: screen_ui import cleanly where storyboard is absent (the OSS carve withholds gtm_core.storyboard).
#: 2026-10-19 +75 (4828->4903): video craft hook: result channel + guarded video_craft call; craft
#: logic lives in gtm_core/video_craft (withheld). gtm_core/shots_lint/result.py adds
#: `lint_shotlist_detailed` (Finding/LintResult, `not_checked`) around the UNCHANGED
#: `lint_shotlist`, and cli.py exposes it; no craft rule is written here (PRD
#: 2026-10-19-video-story-asset-motion-craft R8). Also +13 in screen_ui.py: the scene-name check
#: against the screen_ui registry that the schema text already claimed — a missing structural
#: check, not a new text-prompt rule.
#: 2026-10-19 +2 (4903->4905): the craft-hook import guard names its two absent-module cases (a
#: missing module INSIDE video_craft now raises instead of reading as a clean list), and the
#: --require-craft help text says what the flag does in plain words.
#: 2026-10-19 +13 (4905->4918): the craft hook is handed the shot list's own folder (`base_dir`) so a
#: diagram shot's labels can be read from its file and counted on screen; most of the growth is
#: the formatter wrapping the widened signatures onto several lines.
#: 2026-10-21 +9 (4918->4927): no new rule. Three lint paths imported the withheld video_finish
#: package unguarded and crashed the public cut; each now degrades on ImportError.
MAX_PRERENDER_LINES = 4927

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
