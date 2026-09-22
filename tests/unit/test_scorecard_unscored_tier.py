"""A categorised row must never be mistaken for a weak one.

``gtm_core.scorecard`` introduces ``tier: "unscored"`` — the tier a row carries when an input was
absent and no rubric ever ran. The PRD promises such a row "cannot surface as a weak finalist".
That was not true by default: nothing downstream crashed on the new value, and *that was the
problem* — every reader either compared it against a list it was not on and fell through to the
permissive branch, or stored it verbatim.

Four sites, each one line, each with its own test here:

* it must not be swept into the scorer's ``dropped`` bucket, which stamps ``verdict: drop`` with
  reason "below publish threshold" — a sentence about a number the row never had;
* it must not carry a ``score`` key at all, because ``sort_key_finalist`` calls ``float()`` on it;
* it must not pass ``prospect_status_receipt``'s fit gate, which tested ``("C", "DROP")`` only and
  waved it on toward enrolment;
* it must not count as "scored" in the dashboard funnel, which reported the backlog as worked.

Fixtures are fictional per §R9.
"""

from __future__ import annotations

import pytest

from gtm_core.prospect_status_receipt import _fails_fit
from gtm_core.score_prospects import (
    UNSCORED_TIER,
    score_and_rank_prospects,
    score_and_rank_prospects_with_dropped,
    sort_key_finalist,
)

CATEGORISED = {
    "company": "Aldermoor Labs",
    "segment": "startup",
    "score_category": "Unscored — agent activity not assessed",
    "score_missing_input": "agent_evidence",
}
SCORED = {"company": "Wrenfield Systems", "segment": "startup", "fit_score": 9}
WEAK = {"company": "Caldermere Group", "segment": "startup", "fit_score": 1}


def _buckets(rows):
    return score_and_rank_prospects_with_dropped(rows)


# --------------------------------------------------------------------------------------------
# 1. The third bucket
# --------------------------------------------------------------------------------------------


def test_a_categorised_row_is_neither_published_nor_dropped() -> None:
    published, dropped, unscored = _buckets([SCORED, WEAK, CATEGORISED])
    assert [r["company"] for r in unscored] == ["Aldermoor Labs"]
    assert "Aldermoor Labs" not in {r["company"] for r in published}
    assert "Aldermoor Labs" not in {r["company"] for r in dropped}


def test_a_categorised_row_never_acquires_a_drop_verdict() -> None:
    """The specific confusion: ``verdict: drop`` + "below publish threshold" says we measured
    the account and it came up short. We did not measure it at all."""
    _published, _dropped, unscored = _buckets([CATEGORISED])
    row = unscored[0]
    assert row.get("verdict") != "drop"
    assert "below publish threshold" not in str(row.get("verdict_reason") or "")
    assert row["score_category"] == CATEGORISED["score_category"]
    assert row["tier"] == UNSCORED_TIER


def test_every_candidate_lands_in_exactly_one_bucket() -> None:
    rows = [SCORED, WEAK, CATEGORISED, dict(CATEGORISED, company="Pallister Freight")]
    published, dropped, unscored = _buckets(rows)
    assert len(published) + len(dropped) + len(unscored) == len(rows)


def test_the_single_list_helper_refuses_rather_than_dropping_a_row() -> None:
    """``score_and_rank_prospects`` returns ONE list, so it has nowhere to put a categorised
    row. Refusing keeps the denominator honest; returning the published rows alone would shrink
    it silently."""
    with pytest.raises(ValueError, match="categorised"):
        score_and_rank_prospects([SCORED, CATEGORISED])
    assert score_and_rank_prospects([SCORED])  # positive control: it still works normally


# --------------------------------------------------------------------------------------------
# 2. It carries no score, and it is never ranked
# --------------------------------------------------------------------------------------------


def test_a_categorised_row_carries_no_score_key_at_all() -> None:
    """Not ``None``: ``sort_key_finalist`` does ``float(item.get("score", 0))``, and the default
    only fires on a MISSING key. A present-and-null score is a TypeError."""
    _p, _d, unscored = _buckets([CATEGORISED])
    assert "score" not in unscored[0]
    assert "base_score" not in unscored[0]
    sort_key_finalist(unscored[0])  # must not raise


def test_a_null_score_would_have_crashed_the_sort() -> None:
    """Negative control for the test above — proof the missing key is load-bearing rather than
    incidental."""
    with pytest.raises(TypeError):
        sort_key_finalist({"tier": UNSCORED_TIER, "score": None})


def test_a_categorised_row_is_not_in_the_ranked_finalist_queue() -> None:
    published, _d, _u = _buckets([CATEGORISED, SCORED])
    assert [r["company"] for r in published] == ["Wrenfield Systems"]


# --------------------------------------------------------------------------------------------
# 3. The fit gate
# --------------------------------------------------------------------------------------------


def test_an_unscored_row_does_not_pass_the_fit_gate() -> None:
    """Before 2026-09-22 this gate tested ``("C", "DROP")`` only, so an UNSCORED row passed it
    and nothing further downstream stopped it reaching a recipient."""
    assert _fails_fit({"tier": "unscored"}) is True
    assert _fails_fit({"tier": "UNSCORED"}) is True


def test_the_fit_gate_still_passes_a_real_finalist() -> None:
    """Positive control. A gate that refuses everything passes the test above."""
    assert _fails_fit({"tier": "A", "verdict": "send"}) is False


# --------------------------------------------------------------------------------------------
# 4. The funnel count
# --------------------------------------------------------------------------------------------


def test_an_unscored_row_is_not_counted_as_scored_in_the_funnel(tmp_path) -> None:
    """``if tier or score is not None`` read a truthy "UNSCORED" as scored, reporting rows that
    are still waiting on an input as work already done."""
    import json

    from gtm_core.prospects_dashboard import _latest_tokens

    profile = "fixtureco"
    root = tmp_path / profile / "prospects"
    root.mkdir(parents=True)
    (root / "latest.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "company": "Aldermoor Labs",
                        "domain": "aldermoor.example",
                        "tier": "unscored",
                    },
                    {
                        "company": "Wrenfield Systems",
                        "domain": "wrenfield.example",
                        "tier": "A",
                        "score": 9,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    all_tokens, scored, tier_a = _latest_tokens(profile, tmp_path)
    assert len(all_tokens) == 2
    assert len(scored) == 1, "an unscored row was counted as scored"
    assert len(tier_a) == 1
