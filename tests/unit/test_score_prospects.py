"""Unit tests for gtm_core.score_prospects (Step 8 deterministic scoring)."""

from __future__ import annotations

import json
from pathlib import Path

from gtm_core.score_prospects import (
    evaluate_heat,
    main,
    score_and_rank_prospects,
    score_candidate,
)


def test_evaluate_heat_zero_when_no_intent():
    heat, feeds, is_elevated = evaluate_heat(intent_data={})
    assert heat == 0
    assert feeds == []
    assert not is_elevated


def test_evaluate_heat_elevated_is_not_heat():
    heat, feeds, is_elevated = evaluate_heat(intent_data={"vibe": 68})
    assert heat == 0
    assert feeds == []
    assert is_elevated


def test_evaluate_heat_single_feed_high_intent():
    heat, feeds, is_elevated = evaluate_heat(intent_data={"vibe-topic": 85})
    assert heat == 2
    assert feeds == ["vibe-topic"]
    assert not is_elevated


def test_evaluate_heat_double_intent_convergence():
    heat, feeds, is_elevated = evaluate_heat(intent_data={"vibe-topic": 85, "rr-intent": 78})
    assert heat == 3
    assert set(feeds) == {"vibe-topic", "rr-intent"}


def test_evaluate_heat_three_feeds_still_double_intent_cap():
    heat, feeds, _ = evaluate_heat(
        intent_data={"vibe-topic": 85, "rr-intent": 78, "apollo-intent": 90}
    )
    assert heat == 3
    assert len(feeds) == 3


def test_evaluate_heat_from_list_of_topics():
    topics = [
        {"topic": "agentic ai", "score": 86},
        {"topic": "mlops", "score": 62},
    ]
    heat, feeds, is_elevated = evaluate_heat(intent_data=topics)
    assert heat == 2
    assert feeds == ["agentic ai"]


def test_score_candidate_caps_at_ceiling():
    # Startup default ceiling is 10
    cand = {
        "company": "FastScale AI",
        "segment": "startup",
        "base_score": 9,
        "intent_scores": {"vibe": 80, "rr": 80},  # heat = 3
    }
    res = score_candidate(cand)
    assert res["heat"] == 3
    # 9 + 3 = 12, capped at 10
    assert res["score"] == 10
    assert res["tier"] == "A"
    assert res["priority"] == "high"


def test_score_candidate_tier_thresholds():
    # Startup: publish >= 6, tier_a >= 7
    c_a = {"company": "Company A", "segment": "startup", "base_score": 7}
    assert score_candidate(c_a)["tier"] == "A"

    c_b = {"company": "Company B", "segment": "startup", "base_score": 6}
    assert score_candidate(c_b)["tier"] == "B"

    c_drop = {"company": "Company C", "segment": "startup", "base_score": 5}
    assert score_candidate(c_drop)["tier"] == "drop"
    assert score_candidate(c_drop)["priority"] == "low"


def test_finalist_ranking_order():
    candidates = [
        {
            "company": "B-Tier High Score",
            "segment": "startup",
            "base_score": 6,
            "signal_observed": "2026-09-01",
        },
        {
            "company": "A-Tier Heat 0",
            "segment": "startup",
            "base_score": 7,
            "signal_observed": "2026-09-01",
        },
        {
            "company": "A-Tier Heat 3",
            "segment": "startup",
            "base_score": 7,
            "intent_scores": {"vibe": 80, "rr": 80},
            "signal_observed": "2026-08-01",
        },
        {
            "company": "A-Tier Heat 2 New In Role",
            "segment": "startup",
            "base_score": 7,
            "intent_scores": {"vibe": 80},
            "new_in_role": True,
            "signal_observed": "2026-08-01",
        },
        {
            "company": "A-Tier Heat 2 Standard Role Recent Date",
            "segment": "startup",
            "base_score": 7,
            "intent_scores": {"vibe": 80},
            "new_in_role": False,
            "signal_observed": "2026-09-10",
        },
    ]

    ranked = score_and_rank_prospects(candidates)
    names = [item["company"] for item in ranked]

    # Expected order:
    # 1. Tier A, Heat 3
    # 2. Tier A, Heat 2, new_in_role True
    # 3. Tier A, Heat 2, new_in_role False, Date 2026-09-10
    # 4. Tier A, Heat 0
    # 5. Tier B
    assert names == [
        "A-Tier Heat 3",
        "A-Tier Heat 2 New In Role",
        "A-Tier Heat 2 Standard Role Recent Date",
        "A-Tier Heat 0",
        "B-Tier High Score",
    ]


def test_cli_execution(tmp_path: Path):
    input_file = tmp_path / "candidates.json"
    output_file = tmp_path / "ranked.json"

    data = [
        {"company": "Alpha Corp", "segment": "enterprise", "base_score": 8},
        {"company": "Beta Inc", "segment": "enterprise", "base_score": 5},
    ]
    input_file.write_text(json.dumps(data), encoding="utf-8")

    code = main(["--items", str(input_file), "--out", str(output_file)])
    assert code == 0

    ranked = json.loads(output_file.read_text(encoding="utf-8"))
    assert len(ranked) == 2
    assert ranked[0]["company"] == "Alpha Corp"
    assert ranked[0]["tier"] == "A"
    assert ranked[1]["company"] == "Beta Inc"
    assert ranked[1]["tier"] == "drop"
