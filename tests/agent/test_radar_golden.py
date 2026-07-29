"""Golden-path test for content-radar's deterministic core (spec §7.1, §13.3, plan 1.8).

Feeds the canned ``discovery_items`` fixture through ``agent.radar`` and asserts:
  - every emitted StoryCluster validates against schemas/story-cluster.schema.json
  - scores are within [0, 100] and monotonic in trending_score (fit/relevance equal)
  - dedupe removes ids already present in history.jsonl
  - near-duplicate-title rows merge into one cluster
  - render_digest produces a non-empty digest referencing the clusters

SDK-INDEPENDENT: agent.radar is pure stdlib, so this runs in CI even when the
editable install (claude-agent-sdk) is unavailable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Defer import so a missing sibling module skips rather than collection-errors.
pytest.importorskip("agent.radar", reason="agent.radar not built yet")

from agent import radar  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"
SCHEMAS = REPO / "schemas"

# Vendored, dependency-free validator (same one the contract tests use).
sys.path.insert(0, str(REPO / "tests" / "contracts"))
from minijsonschema import validate  # noqa: E402

PILLARS = ["AI Trust", "AI Policy", "Agentic AI"]


def _rows() -> list[dict]:
    return json.loads((FIXTURES / "discovery_items.sample.json").read_text())


def _story_schema() -> dict:
    return json.loads((SCHEMAS / "story-cluster.schema.json").read_text())


def test_fixture_clusters_are_schema_valid():
    items = radar.news_from_rows(_rows())
    clusters = radar.cluster_and_score(items, PILLARS)
    assert clusters, "fixture must yield at least one cluster"
    schema = _story_schema()
    for c in clusters:
        errs = validate(c, schema)
        assert errs == [], f"{c['id']} invalid: {errs}"


def test_scores_in_range_and_sorted_descending():
    items = radar.news_from_rows(_rows())
    clusters = radar.cluster_and_score(items, PILLARS)
    scores = [c["score"] for c in clusters]
    assert all(0 <= s <= 100 for s in scores)
    assert scores == sorted(scores, reverse=True), "clusters must be ranked by score desc"


def test_score_monotonic_in_trending_score():
    """Same pillar-fit + relevance, differing only in trending_score → higher trending scores higher.

    Titles differ enough to NOT merge (Jaccard < 0.6) but both map to the Agentic
    AI pillar with full relevance, isolating trending_score as the only variable.
    """
    low = {
        "id": "low",
        "title": "Agentic AI framework alpha lands for developers",
        "summary": "An agentic AI framework milestone.",
        "topics": ["agentic-ai"],
        "trending_score": 10,
    }
    high = {
        "id": "high",
        "title": "Agentic AI framework beta arrives with new tools",
        "summary": "An agentic AI framework milestone.",
        "topics": ["agentic-ai"],
        "trending_score": 95,
    }
    items = radar.news_from_rows([low, high])
    clusters = radar.cluster_and_score(items, PILLARS)
    by_src = {c["source_items"][0]: c["score"] for c in clusters}
    assert set(by_src) == {"low", "high"}, "distinct-title rows must not merge"
    assert by_src["high"] > by_src["low"], (
        "a much higher trending_score should score strictly higher"
    )


def test_dedupe_drops_seen_ids(tmp_path):
    history = tmp_path / "history.jsonl"
    # An id the radar previously surfaced (as source_items in a history entry).
    history.write_text(
        json.dumps(
            {
                "event": "radar_complete",
                "source_items": ["11111111-1111-1111-1111-111111111111"],
            }
        )
        + "\n"
    )
    seen = radar.seen_ids_from_history(history)
    assert "11111111-1111-1111-1111-111111111111" in seen

    items = radar.news_from_rows(_rows())
    kept = radar.dedupe(items, seen)
    kept_ids = {it["id"] for it in kept}
    assert "11111111-1111-1111-1111-111111111111" not in kept_ids
    assert len(kept) == len(items) - 1


def test_near_duplicate_titles_merge_into_one_cluster():
    items = radar.news_from_rows(_rows())
    clusters = radar.cluster_and_score(items, PILLARS)
    # Rows 1 and 6 are the same story from two outlets — they must share a cluster.
    dup = {"11111111-1111-1111-1111-111111111111", "66666666-6666-6666-6666-666666666666"}
    assert any(dup <= set(c["source_items"]) for c in clusters), "duplicate-title rows must merge"


def test_render_digest_non_empty_and_references_clusters():
    items = radar.news_from_rows(_rows())
    clusters = radar.cluster_and_score(items, PILLARS)
    digest = radar.render_digest(clusters, items, "2026-06-13", "example")
    assert "Content Radar" in digest
    assert clusters[0]["pillar"] in digest
    # Every cluster id should appear in the digest.
    for c in clusters:
        assert c["id"] in digest


def test_empty_inputs_yield_no_clusters():
    assert radar.cluster_and_score([], PILLARS) == []
    assert radar.cluster_and_score(radar.news_from_rows(_rows()), []) == []
