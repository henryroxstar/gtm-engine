"""A score that reaches ``latest.json`` must name the rubric that produced it.

A score is a claim, and until 2026-09-22 nothing recorded what it was a claim *against*. A
1003-row pass scored every account on a rubric assembled from plan prose while the tenant's
maintained one sat unread in the profile; the resulting numbers merged into the ledger looking
exactly like numbers from the real rubric. Nothing downstream could tell them apart — not review,
not the dashboard, and not ``outcomes-sync``, which is why a rubric change still cannot be
attributed to a reply-rate change.

``finalize`` now refuses. The important half is *when*: before anything is written, so a refused
batch leaves no half-merged ledger behind.

Fixtures are fictional per §R9.
"""

from __future__ import annotations

import json

import pytest

from gtm_core.prospects_import import (
    STRICT_PROVENANCE_ENV,
    finalize,
    require_rubric_provenance,
    strict_provenance,
)

RUBRIC = {"rubric_source": "knowledge/fixture.md#rubric", "rubric_version": "2026-01-01"}
SCORED = {"company": "Aldermoor Labs", "segment": "startup", "score": 8, "tier": "A", **RUBRIC}
UNSCORED = {
    "company": "Pallister Freight",
    "segment": "startup",
    "score_category": "Unscored — no buyer-intent reading",
    "tier": "unscored",
}


# --------------------------------------------------------------------------------------------
# 1. The refusal
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["rubric_source", "rubric_version"])
def test_a_scored_item_without_provenance_is_refused(field: str) -> None:
    item = {k: v for k, v in SCORED.items() if k != field}
    with pytest.raises(ValueError, match=field):
        require_rubric_provenance([item])


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_a_blank_rubric_value_is_not_provenance(blank) -> None:
    """A field that is present but empty asserts nothing. It must refuse exactly as a missing
    one does, or "declare it" becomes "add the key"."""
    with pytest.raises(ValueError, match="rubric_version"):
        require_rubric_provenance([dict(SCORED, rubric_version=blank)])


def test_an_unscored_item_is_not_asked_for_provenance() -> None:
    """A categorised row makes no claim, so there is no rubric for it to name. Requiring one
    would make the honest outcome harder to record than the confident one."""
    require_rubric_provenance([UNSCORED])


def test_a_declared_score_passes() -> None:
    """Positive control. A gate that refuses everything passes every test above."""
    require_rubric_provenance([SCORED, UNSCORED])


def test_the_message_names_the_row_the_fields_and_the_fix() -> None:
    """§5 UAT: "does it say WHICH field is missing and where to declare it, or is it a
    traceback?" """
    with pytest.raises(ValueError) as caught:
        require_rubric_provenance([dict(SCORED, rubric_source="", rubric_version="")])
    message = str(caught.value)
    assert "Aldermoor Labs" in message
    assert "rubric_source" in message and "rubric_version" in message
    assert "gtm_core.scorecard" in message
    assert "Nothing was written" in message


def test_many_gaps_are_summarised_rather_than_listed_forever() -> None:
    rows = [dict(SCORED, company=f"Fixture {n}", rubric_version="") for n in range(40)]
    with pytest.raises(ValueError) as caught:
        require_rubric_provenance(rows)
    assert "and 35 more" in str(caught.value)


# --------------------------------------------------------------------------------------------
# 2. It refuses BEFORE it writes
# --------------------------------------------------------------------------------------------


def test_a_refused_batch_writes_nothing(tmp_path) -> None:
    """The half-merge is the danger: a ledger that holds the first N rows of a batch nobody
    accepted is worse than one that holds none of them."""
    profile = "fixtureco"
    (tmp_path / profile / "prospects").mkdir(parents=True)
    with pytest.raises(ValueError, match="rubric"):
        finalize(
            profile,
            [SCORED, {k: v for k, v in SCORED.items() if k != "rubric_source"}],
            "run-1",
            content_root=tmp_path,
        )
    assert not (tmp_path / profile / "prospects" / "latest.json").exists()
    assert not list((tmp_path / profile / "prospects").glob("*.csv"))


def test_a_declared_batch_reaches_the_ledger(tmp_path) -> None:
    """The other half — the refusal must not be the only thing that works."""
    profile = "fixtureco"
    (tmp_path / profile / "prospects").mkdir(parents=True)
    finalize(profile, [SCORED], "run-1", content_root=tmp_path)
    written = json.loads(
        (tmp_path / profile / "prospects" / "latest.json").read_text(encoding="utf-8")
    )
    row = written["items"][0]
    assert row["company"] == "Aldermoor Labs"
    assert row["rubric_source"] == RUBRIC["rubric_source"]
    assert row["rubric_version"] == RUBRIC["rubric_version"]


def test_a_categorised_row_keeps_its_category_through_the_merge(tmp_path) -> None:
    """The category is the operator's work queue: it names the input the row is waiting on. If
    it does not survive to ``latest.json`` the queue does not exist."""
    profile = "fixtureco"
    (tmp_path / profile / "prospects").mkdir(parents=True)
    finalize(profile, [UNSCORED], "run-1", content_root=tmp_path)
    row = json.loads(
        (tmp_path / profile / "prospects" / "latest.json").read_text(encoding="utf-8")
    )["items"][0]
    assert row["score_category"] == UNSCORED["score_category"]


# --------------------------------------------------------------------------------------------
# 3. The kill switch, and which way it fails
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF"])
def test_a_recognised_false_opens_the_migration_window(monkeypatch, value: str) -> None:
    monkeypatch.setenv(STRICT_PROVENANCE_ENV, value)
    assert strict_provenance() is False
    require_rubric_provenance([{k: v for k, v in SCORED.items() if k != "rubric_source"}])


@pytest.mark.parametrize("value", ["", "  ", "true", "1", "wharrgarbl", "disabled"])
def test_anything_unrecognised_leaves_the_gate_CLOSED(monkeypatch, value: str) -> None:
    """This switch disables a refusal, so "off" is the permissive side and an unrecognised value
    must never land there. ``disabled`` reads as off to a human and is deliberately not
    honoured — a closed list is only safe if it is closed in the direction that matters."""
    monkeypatch.setenv(STRICT_PROVENANCE_ENV, value)
    assert strict_provenance() is True
    with pytest.raises(ValueError):
        require_rubric_provenance([{k: v for k, v in SCORED.items() if k != "rubric_source"}])


def test_an_unset_switch_leaves_the_gate_closed(monkeypatch) -> None:
    monkeypatch.delenv(STRICT_PROVENANCE_ENV, raising=False)
    assert strict_provenance() is True
