"""Contract: the pattern_id attribution axis stays wired end to end, in the skill bodies.

`gtm_core.tweet_patterns` + `gtm_core.hooks.OpeningBeat.pattern_id` + `gtm_distill`'s `by_pattern`
axis are unit-tested elsewhere (tests/skills/test_tweet_patterns.py, tests/test_hooks.py,
tests/test_hooks_lint.py, tests/test_gtm_distill.py) and all pass with zero data. That proves the
machinery works, not that anything ever calls it — exactly the failure shape
tests/contracts/test_brief_lint_wired.py documents: a gate that stops running stays green,
because nothing checks that it still runs.

Two skill bodies are the only things that make pattern_id data ever exist: content-studio must
choose one and write it into the asset JSON, and content-outcomes-sync must read it back and tag
the outcome row `pattern:<id>`. Delete either instruction and gtm_distill's by_pattern axis is
permanently empty while every other test in the suite stays green.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
STUDIO_BODY = REPO / "plugin" / "skills" / "content-studio" / "body_template.md"
OUTCOMES_SYNC_BODY = REPO / "plugin" / "skills" / "content-outcomes-sync" / "body_template.md"

_outcomes_sync_stubbed = pytest.mark.skipif(
    not OUTCOMES_SYNC_BODY.exists(),
    reason="content-outcomes-sync body_template.md not present (paid-tier stub)",
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_studio_body_instructs_choosing_and_writing_pattern_id():
    body = _text(STUDIO_BODY)
    assert "docs/x-tweet-patterns.md" in body
    assert "pattern_id" in body
    # The operator-choice discipline, mirrored from hook-craft's Workflow — not a silent pick.
    assert "3 candidates across distinct patterns" in body
    # It must actually land in the asset JSON, the only channel content-outcomes-sync reads.
    assert "pattern_id" in body.split("Metadata.")[-1][:400]


def test_studio_body_names_the_catalog_fit_vocabulary():
    """A drafter that never reads --fit core vs conditional can't apply a guardrail it doesn't
    know exists."""
    body = _text(STUDIO_BODY)
    assert "--fit core" in body
    assert "guardrail" in body


@_outcomes_sync_stubbed
def test_outcomes_sync_body_reads_and_tags_pattern_id():
    body = _text(OUTCOMES_SYNC_BODY)
    assert "pattern_id" in body
    assert "--tag pattern:<pattern_id>" in body
    assert "pattern_performance.json" in body


@_outcomes_sync_stubbed
def test_outcomes_sync_body_sources_pattern_id_from_the_asset_json():
    """The tag must be read from where content-studio actually writes it, not invented."""
    body = _text(OUTCOMES_SYNC_BODY)
    assert ".asset.json" in body
    assert "top-level `pattern_id`" in body
