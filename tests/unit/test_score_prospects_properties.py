"""Properties the Step 8 scorer must hold — written against what the prospect skill CLAIMS it does.

`tests/unit/test_score_prospects.py` checks the arithmetic on well-formed fixtures. Every fixture
there carries a date, one hot topic, and a `base_score`, so none of the behaviours below could be
seen: a check that cannot discriminate is not a check (§R18). These fixtures are the ones that do.

Defects PSK-003 through PSK-010 (from the 2026-09-21 prospect skill E2E audit) are now fixed; the
tests below are plain regression tests guarding against a repeat, not xfails.
All data is fictional.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from gtm_core.score_prospects import (
    evaluate_heat,
    main,
    score_and_rank_prospects,
    score_candidate,
)

REPO = Path(__file__).resolve().parents[2]


def _cand(company: str = "Northwind Robotics", **kw) -> dict:
    return {"company": company, "segment": "startup", **kw}


# --------------------------------------------------------------------------- properties that hold


def test_idempotent_when_the_fit_score_is_carried() -> None:
    once = score_and_rank_prospects([_cand(fit_score=5, intent_scores={"vibe-topic": 80})])
    twice = score_and_rank_prospects(once)
    assert (once[0]["score"], once[0]["tier"]) == (twice[0]["score"], twice[0]["tier"])


def test_elevated_intent_never_adds_heat() -> None:
    heat, feeds, elevated = evaluate_heat(intent_data={"vibe-topic": 74})
    assert (heat, feeds, elevated) == (0, [], True)


def test_score_never_exceeds_the_ceiling() -> None:
    out = score_and_rank_prospects([_cand(fit_score=10, intent_scores={"a": 90, "b": 90})])
    assert out[0]["score"] == 10


def test_two_distinct_feeds_are_double_intent() -> None:
    heat, feeds, _ = evaluate_heat(intent_data={"vibe-topic": 80, "rr-intent": 78})
    assert heat == 3 and len(feeds) == 2


# --------------------------------------------------------------------------- formerly known defects


def test_idempotent_on_the_ledger_item_shape() -> None:
    """`latest.json` items carry `score` (heat already in it) and no `base_score`.

    Re-score mode and any resumed run feed exactly that shape back in, so a Tier-B account was
    promoted to Tier-A by being scored twice. PSK-003: `base_score` is now always written back
    (holding the pre-heat value), so re-scoring reads it in preference to the heat-inflated
    `score` and stays idempotent.
    """
    once = score_and_rank_prospects(
        [_cand(segment="enterprise", score=5, intent_scores={"vibe-topic": 80})]
    )
    twice = score_and_rank_prospects(once)
    assert (once[0]["score"], once[0]["tier"]) == (twice[0]["score"], twice[0]["tier"])


def test_two_topics_from_one_feed_are_not_double_intent() -> None:
    """The bulk-mode item shape (`intent_topics`) lists topics of ONE feed; the skill defines
    double-intent as two or more FEEDS converging. PSK-004: an item with no explicit `feed`
    key is a topic, not a feed, so both share the single "topic-intent" feed."""
    topics = [{"topic": "agentic ai", "score": 86}, {"topic": "ai governance", "score": 80}]
    heat, _feeds, _ = evaluate_heat(intent_data=topics)
    assert heat == 2


def test_list_item_with_explicit_feed_key_enables_double_intent() -> None:
    """PSK-004's fix must not disable real double-intent — two DISTINCT explicit feeds still
    earn heat 3."""
    topics = [
        {"feed": "vibe", "topic": "agentic ai", "score": 86},
        {"feed": "rocketreach", "topic": "ai governance", "score": 80},
    ]
    heat, feeds, _ = evaluate_heat(intent_data=topics)
    assert heat == 3
    assert set(feeds) == {"vibe", "rocketreach"}


def test_feeds_without_any_score_earn_no_heat() -> None:
    """PSK-005: `intent_feeds` with no `top_intent_score` used to be assumed to score 100.

    Scored via `score_candidate` directly (not the list-level `score_and_rank_prospects`) —
    at fit_score=5 this row is correctly below the publish threshold once heat is fixed, and
    PSK-008 means the list-level function would no longer return a below-threshold row at all.
    """
    out = score_candidate(_cand(fit_score=5, intent_feeds=["rr-news", "rr-jobs"]))
    assert out["heat"] == 0 and out["tier"] != "A"
    assert out.get("intent_unscored") is True


def test_an_undated_row_ranks_after_a_dated_one() -> None:
    """PSK-006: an undated row used to outrank a freshly dated one (empty string sorts first)."""
    rows = [
        _cand("Dated Fresh", fit_score=8, signal_observed="2026-09-18"),
        _cand("No Date At All", fit_score=8),
    ]
    assert [r["company"] for r in score_and_rank_prospects(rows)] == [
        "Dated Fresh",
        "No Date At All",
    ]


def test_a_unicode_hyphen_in_a_date_does_not_crash() -> None:
    """PSK-007: non-ASCII date characters (LLM-typical) used to crash the sort key."""
    score_and_rank_prospects([_cand(fit_score=8, signal_observed="2026‑09‑18")])


def test_below_threshold_rows_are_not_emitted() -> None:
    """PSK-008: the skill says below-threshold rows are dropped; `score_and_rank_prospects`
    now actually drops them instead of emitting tier="drop" rows downstream."""
    out = score_and_rank_prospects(
        [_cand("Below Threshold Co", fit_score=2), _cand("Good Co", fit_score=8)]
    )
    assert [r["company"] for r in out] == ["Good Co"]


def test_a_non_object_item_is_refused_cleanly() -> None:
    """PSK-009: untrusted LLM JSON — a non-object item used to raise AttributeError deep
    inside the loop. It's now a clean ValueError naming the index."""
    try:
        score_and_rank_prospects(["Northwind Robotics"])  # type: ignore[list-item]
    except (ValueError, TypeError) as exc:
        assert "index 0" in str(exc)
    else:
        raise AssertionError("expected a ValueError for a non-object candidate")


def test_a_non_numeric_score_is_refused_cleanly() -> None:
    """PSK-009: a NaN/non-numeric fit score must be refused, not silently scored 0."""
    try:
        score_and_rank_prospects([_cand(fit_score="not-a-number")])
    except (ValueError, TypeError) as exc:
        assert "Northwind Robotics" in str(exc)
    else:
        raise AssertionError("expected a ValueError for a non-numeric score")


def test_a_nan_fit_score_is_refused_cleanly_by_the_cli(tmp_path: Path) -> None:
    """PSK-009 catches a NaN fit score before PSK-010's `allow_nan=False` output guard would
    ever need to. The CLI exits 1 with a one-line stderr message — no traceback."""
    items = tmp_path / "items.json"
    items.write_text(json.dumps([_cand(fit_score="NaN")]), encoding="utf-8")
    p = subprocess.run(
        [sys.executable, "-m", "gtm_core.score_prospects", "--items", str(items)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert p.returncode == 1
    assert p.stderr.strip()
    assert "Traceback" not in p.stderr


def test_output_is_always_strict_json(tmp_path: Path) -> None:
    """PSK-010: a stray NaN anywhere in a row (not just the validated fit-score field) must
    never be emitted as the bare, non-JSON token `NaN` — `allow_nan=False` refuses it
    cleanly instead."""
    items = tmp_path / "items.json"
    items.write_text(
        json.dumps([_cand(fit_score=8, unrelated_metric=float("nan"))]), encoding="utf-8"
    )
    p = subprocess.run(
        [sys.executable, "-m", "gtm_core.score_prospects", "--items", str(items)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert p.returncode == 1
    assert "Traceback" not in p.stderr
    assert "NaN" not in p.stdout


# ------------------------------------------------------------------------------- CLI/library extras


def test_dropped_out_flag_writes_below_threshold_rows(tmp_path: Path) -> None:
    items = tmp_path / "items.json"
    out_file = tmp_path / "out.json"
    dropped_file = tmp_path / "dropped.json"
    data = [_cand("Good Co", fit_score=8), _cand("Below Co", fit_score=2)]
    items.write_text(json.dumps(data), encoding="utf-8")

    code = main(["--items", str(items), "--out", str(out_file), "--dropped-out", str(dropped_file)])
    assert code == 0

    published = json.loads(out_file.read_text(encoding="utf-8"))
    assert [r["company"] for r in published] == ["Good Co"]

    dropped = json.loads(dropped_file.read_text(encoding="utf-8"))
    assert len(dropped) == 1
    assert dropped[0]["company"] == "Below Co"
    assert dropped[0]["tier"] == "drop"
    assert dropped[0]["verdict"] == "drop"
    assert dropped[0]["verdict_reason"] == "below publish threshold"


def test_stderr_summary_line(tmp_path: Path, capsys) -> None:
    items = tmp_path / "items.json"
    data = [
        _cand("A Co", fit_score=8),
        _cand("B Co", fit_score=6),
        _cand("Drop Co", fit_score=2),
    ]
    items.write_text(json.dumps(data), encoding="utf-8")

    code = main(["--items", str(items)])
    assert code == 0

    err = capsys.readouterr().err
    assert "scored 3" in err
    assert "published 2" in err
    assert "A: 1, B: 1" in err
    assert "below threshold 1" in err


def test_unknown_segment_prints_one_warning_per_distinct_value(capsys) -> None:
    score_and_rank_prospects(
        [
            _cand("Builder One", segment="builder", fit_score=8),
            _cand("Builder Two", segment="builder", fit_score=8),
            _cand("Mid Co", segment="mid-market", fit_score=8),
        ]
    )
    err = capsys.readouterr().err
    assert err.count("'builder'") == 1
    assert "'mid-market'" in err


def test_tier_a_threshold_zero_is_not_ignored(tmp_path: Path) -> None:
    """PSK-027: `--tier-a-threshold 0` used to be ignored by a truthiness check."""
    items = tmp_path / "items.json"
    items.write_text(json.dumps([_cand(fit_score=1)]), encoding="utf-8")
    out_file = tmp_path / "out.json"

    code = main(["--items", str(items), "--out", str(out_file), "--tier-a-threshold", "0"])
    assert code == 0
    ranked = json.loads(out_file.read_text(encoding="utf-8"))
    assert ranked[0]["tier"] == "A"
