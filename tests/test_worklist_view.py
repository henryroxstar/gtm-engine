"""The worklist groups every account exactly once, and never calls a candidate 'enrolled'.

All fixtures invented (docs/RULES.md R9) — no real recipient appears here.

Regression, found by the panel itself on the day it was written (2026-09-09): the first
version read each sequence's recipient CSV and treated membership as enrolment. That CSV is a
CANDIDATE list — on the SG-builders sequence it holds 10 rows while the provider reports 4
enrolled — so five Gate-B `drop` accounts rendered under "Staged in the sequencer". A reader
acting on that panel would have believed disqualified accounts were loaded and waiting.

Two properties are pinned here, and they are the two the panel exists for:

* **Partition.** Every account appears in exactly one group. A row silently absent from all
  of them makes a short list look complete.
* **The gate decides staged, not the file.** A row the enrollment gate refuses is not staged
  no matter which file it sits in, and the count that disagrees with the provider is
  REPORTED rather than resolved — nothing on disk knows which four of five were loaded.
"""

from __future__ import annotations

import csv

import pytest

from gtm_core.email_campaign_dashboard.views_worklist import (
    GROUPS,
    _group_of,
    _provider_enrolled,
    _staged_candidates,
    _worklist_view,
)

GROUP_IDS = [g for g, _, _ in GROUPS]


def _row(company, *, email="", named=True, verdict="re-angle", tier="B", seat="Founder"):
    return {
        "company": company,
        "seat": seat,
        "email": email,
        "named": named,
        "tier": tier,
        "verdict": verdict,
        "score": "7",
        "signal": False,
        "verdict_reason": "",
        "why_now": "",
        "judge": None,
    }


# ----------------------------------------------------------------- grouping


def test_a_refused_row_on_a_candidate_list_is_not_staged():
    """THE regression. `drop` enrols in no lane, so a drop sitting on a recipient CSV is an
    excluded account that happens to be in a file — never a staged one."""
    candidates = {
        "ok@northgate.example": {"sequence": "seqA", "admissible": True},
        "no@stonebridge.example": {"sequence": "seqA", "admissible": False},
    }
    assert _group_of(_row("Northgate", email="ok@northgate.example"), set(), candidates) == "staged"
    assert (
        _group_of(
            _row("Stonebridge", email="no@stonebridge.example", verdict="drop"), set(), candidates
        )
        == "excluded"
    )


def test_membership_alone_never_makes_a_row_staged():
    """Positive control for the test above: the same row, same file, differing only in
    whether the gate admits it, must land in different groups."""
    row = _row("Brightpath", email="team@brightpath.example", verdict="drop")
    admitted = {"team@brightpath.example": {"sequence": "s", "admissible": True}}
    refused = {"team@brightpath.example": {"sequence": "s", "admissible": False}}
    assert _group_of(row, set(), admitted) != _group_of(row, set(), refused)


@pytest.mark.parametrize(
    "row,expected",
    [
        (_row("Quaymark", email="a@quaymark.example"), "held"),
        (_row("Ashfield", email="hello@ashfield.example", named=False), "handsend"),
        (_row("Larkhill", email=""), "nocontact"),
        (_row("Stonebridge", email="x@stonebridge.example", verdict="drop"), "excluded"),
        (_row("Emptyverdict", email="e@emptyverdict.example", verdict=""), "held"),
    ],
)
def test_each_state_lands_in_its_own_group(row, expected):
    assert _group_of(row, set(), {}) == expected


def test_a_written_pack_outranks_every_other_state():
    """A pack is work already done for a named person; it is what someone would act on."""
    row = _row("Northgate Labs", email="a@northgate.example", verdict="drop")
    assert _group_of(row, {"northgate-labs"}, {}) == "pack"


def test_every_account_lands_in_exactly_one_known_group():
    rows = [
        _row("Northgate", email="a@northgate.example"),
        _row("Ashfield", email="hello@ashfield.example", named=False),
        _row("Larkhill", email=""),
        _row("Stonebridge", email="x@stonebridge.example", verdict="drop"),
    ]
    seen = [_group_of(r, set(), {}) for r in rows]
    assert len(seen) == len(rows), "a row was dropped"
    assert set(seen) <= set(GROUP_IDS), f"unknown group in {seen}"


# ----------------------------------------------------------------- reading the list


def test_the_candidate_reader_marks_admissibility_from_the_verdict(tmp_path, monkeypatch):
    seq = tmp_path / "acme" / "prospects" / "sequences"
    seq.mkdir(parents=True)
    path = seq / "list.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["email", "verdict"])
        w.writeheader()
        w.writerow({"email": "Ok@Northgate.Example", "verdict": "re-angle"})
        w.writerow({"email": "no@stonebridge.example", "verdict": "drop"})
        w.writerow({"email": "blank@quaymark.example", "verdict": ""})

    m = {
        "profile": "acme",
        "_content_root": tmp_path,
        "messages": [{"sequence_id": "seqA", "csv": "list.csv"}],
    }
    out = _staged_candidates(m)
    assert out["ok@northgate.example"]["admissible"] is True, "email must be lowercased to join"
    assert out["no@stonebridge.example"]["admissible"] is False
    # An empty verdict is admissible in the generic lane by design — see lane_verdicts.
    assert out["blank@quaymark.example"]["admissible"] is True


def test_provider_enrolled_reads_the_provider_number_not_the_file():
    m = {
        "campaigns": {
            "campaigns": [
                {"sequences": [{"sequence_id": "seqA", "enrolled": 4}, {"sequence_id": "seqB"}]}
            ]
        }
    }
    assert _provider_enrolled(m) == {"seqA": 4, "seqB": 0}


def test_the_panel_reports_a_list_provider_gap_rather_than_resolving_it():
    """Five admissible rows and four enrolled means one of the five is not loaded, and disk
    cannot say which. Guessing would be confidently wrong about a real person."""
    rows = [_row(f"Co{i}", email=f"a{i}@co{i}.example") for i in range(5)]
    m = {
        "profile": "acme",
        "_content_root": None,
        "roster": {"rows": rows},
        "packs": {"packs": []},
        "messages": [],
        "campaigns": {"campaigns": [{"sequences": [{"sequence_id": "seqA", "enrolled": 4}]}]},
    }
    # No candidate file, so nothing is staged and no gap can be claimed.
    assert "enrolled at the provider" not in _worklist_view(m)

    # With all five on the list, the gap must be stated and must not be silently resolved.
    m["_candidates_override"] = None
    import gtm_core.email_campaign_dashboard.views_worklist as v

    orig = v._staged_candidates
    v._staged_candidates = lambda _m: {
        r["email"].lower(): {"sequence": "seqA", "admissible": True} for r in rows
    }
    try:
        html = v._worklist_view(m)
    finally:
        v._staged_candidates = orig

    assert "5 admissible on the list but 4 enrolled at the provider" in html
    assert "not knowable from disk" in html


def test_a_scope_with_no_roster_says_so_instead_of_listing_the_pool():
    out = _worklist_view({"profile": "acme", "roster": {}, "packs": {}, "messages": []})
    assert "no campaign roster" in out
    assert "<table" not in out, "a rosterless scope must not render an empty table"
