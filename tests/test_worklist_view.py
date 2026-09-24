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
import itertools

import pytest

from gtm_core.email_campaign_dashboard.views_worklist import (
    GROUPS,
    _group_of,
    _provider_enrolled,
    _reconcile,
    _staged_candidates,
    _worklist_view,
)
from gtm_core.prospect_status import LABELS

GROUP_IDS = [g for g, _, _ in GROUPS]


#: Fixture rows are hand-built rather than folded by ``roster_model``, so they need the
#: canonical index it assigns. UNIQUENESS is the property the panel depends on — both
#: tables stamp it as ``data-row`` and the filter hides by it, so two rows sharing an index
#: would hide as one. Contiguity is ``roster_model``'s business and is tested there.
_INDEX = itertools.count()


def _row(company, *, email="", named=True, verdict="re-angle", tier="B", seat="Founder"):
    i = next(_INDEX)
    return {
        "i": i,
        "ci": i,
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
        "ok@northgate.example": {"sequences": ("seqA",), "admissible": True},
        "no@stonebridge.example": {"sequences": (), "admissible": False},
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
    whether the gate admits it, must land in different groups.

    The row carries a SENDABLE verdict on purpose. Written with `drop` this control could
    not discriminate once `_group_of` began reading the account's own verdict too — both
    sides land in `excluded` for that reason rather than the one the test is about, and it
    would pass or fail for nothing to do with membership.
    """
    row = _row("Brightpath", email="team@brightpath.example", verdict="send")
    admitted = {"team@brightpath.example": {"sequences": ("s",), "admissible": True}}
    refused = {"team@brightpath.example": {"sequences": (), "admissible": False}}
    assert _group_of(row, set(), admitted) == "staged"
    assert _group_of(row, set(), admitted) != _group_of(row, set(), refused)


def test_a_stale_blank_on_the_list_cannot_outrank_a_recorded_drop():
    """The account's own verdict is read alongside the recipient file's, and the file is the
    older of the two.

    A recipient CSV written before the research pass carries a BLANK verdict, which the
    generic lane admits by design — blank means "this lane makes no per-row claim", not
    "this account was cleared". Reading only the file let an account the researcher had
    since dropped render as "On the recipient list. Nothing sent.", which is the exact
    sentence `_staged_candidates` calls the most dangerous thing this panel can say.
    """
    blank_list = {"a@northgate.example": {"sequences": ("seqA",), "admissible": True}}
    assert _group_of(_row("Northgate", email="a@northgate.example"), set(), blank_list) == "staged"
    assert (
        _group_of(_row("Northgate", email="a@northgate.example", verdict="drop"), set(), blank_list)
        == "excluded"
    ), "a recorded drop must outrank an admissible-but-stale recipient row"


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


def test_a_written_pack_outranks_every_NOT_ENROLLED_state():
    """A pack is work already done for a named person, so it beats every reason-nothing-is-
    ready state below it — including a `drop` verdict, which would otherwise send this row
    to `excluded`.

    Renamed 2026-09-09. It was `..._outranks_every_other_state`, which stopped being true
    the moment enrolment was given precedence, and the test kept passing because it passes
    an EMPTY candidate map and so never exercises the one state that now beats it. A test
    whose name asserts a property it does not exercise is docs/RULES.md §R18 wearing a
    green tick; `test_enrolment_outranks_a_pack_file_on_disk` below covers the other half.
    """
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
        r["email"].lower(): {"sequences": ("seqA",), "admissible": True} for r in rows
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


def test_enrolment_outranks_a_pack_file_on_disk():
    """A pack on disk records that someone once hand-wrote an email; being on a sequencer's
    list is what is true about the send now.

    Regression 2026-09-09: the 5 Tier-A pack contacts were folded onto the generic arc and
    enrolled, but `pack` was checked first, so the worklist kept filing them under "sent by
    hand" while the provider had them loaded — the page disagreeing with the sequencer about
    six people. The pack files stay on disk deliberately; they are history, not a work state.
    """
    row = _row("Northgate Labs", email="a@northgate.example")
    packs = {"northgate-labs"}
    enrolled = {"a@northgate.example": {"sequences": ("seqA",), "admissible": True}}

    assert _group_of(row, packs, enrolled) == "staged"
    # Control: with no enrolment the same row is still a pack, so this is a precedence
    # change and not a removal of the pack group.
    assert _group_of(row, packs, {}) == "pack"


# ----------------------------------------------------------------- PS14: the Status column


def test_row_html_shows_the_status_word_joined_by_email():
    from gtm_core.email_campaign_dashboard.views_worklist import _row_html

    row = _row("Northgate", email="a@northgate.example", verdict="send")
    m = {
        "prospect_status": {
            "available": True,
            "by_email": {"a@northgate.example": "ready_to_send"},
        }
    }
    assert LABELS["ready_to_send"] in _row_html(m, row, "held", {})


def test_row_html_reads_not_yet_routed_for_an_address_the_router_never_saw():
    """A roster row and a router row are different populations — a miss is ordinary, not
    an error, and must read as a plain sentence rather than a blank cell."""
    from gtm_core.email_campaign_dashboard.views_worklist import _row_html

    row = _row("Northgate", email="a@northgate.example")
    m = {"prospect_status": {"available": True, "by_email": {}}}
    assert "not yet routed" in _row_html(m, row, "held", {})


def test_row_html_reads_a_dash_when_the_router_has_never_run():
    """No `lanes-state.jsonl` at all — a different, stronger refusal than a miss on one
    address: nothing has been routed on this profile, not just this row."""
    from gtm_core.email_campaign_dashboard.views_worklist import _row_html

    row = _row("Northgate", email="a@northgate.example")
    assert "muted" in _row_html({}, row, "held", {})
    assert "muted" in _row_html(
        {"prospect_status": {"available": False, "by_email": {}}}, row, "held", {}
    )


def test_the_worklist_table_carries_a_status_column_and_marks_research_verdict_technical():
    rows = [_row("Northgate", email="a@northgate.example", verdict="send")]
    m = {
        "profile": "acme",
        "_content_root": None,
        "roster": {"rows": rows},
        "packs": {"packs": []},
        "messages": [],
        "campaigns": {"campaigns": [{"sequences": []}]},
        "prospect_status": {
            "available": True,
            "by_email": {"a@northgate.example": "ready_to_send"},
        },
    }
    html = _worklist_view(m)
    assert "<th>Status</th>" in html
    assert '<th class="tech">Research verdict</th>' in html
    assert LABELS["ready_to_send"] in html
    # One column added (Account/Status/Contact/Email/Verified/Research verdict/Where it
    # stands = 7) — the group-header row's colspan must grow with the table or it will not
    # span every column.
    assert 'colspan="7"' in html


# --------------------------------------------- one address, several lists (ordering)


def _two_list_model(tmp_path):
    """One address on two sequences' lists, plus one address unique to each."""
    seq = tmp_path / "acme" / "prospects" / "sequences"
    seq.mkdir(parents=True, exist_ok=True)
    for name, rows in (
        ("a.csv", [("both@northgate.example", "send"), ("onlya@ashfield.example", "send")]),
        ("b.csv", [("both@northgate.example", ""), ("onlyb@quaymark.example", "send")]),
    ):
        with (seq / name).open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["email", "verdict"])
            w.writeheader()
            for email, verdict in rows:
                w.writerow({"email": email, "verdict": verdict})
    return {
        "profile": "acme",
        "_content_root": tmp_path,
        "messages": [
            {"sequence_id": "seqA", "csv": "a.csv"},
            {"sequence_id": "seqB", "csv": "b.csv"},
        ],
    }


def test_the_candidate_reader_is_independent_of_the_order_the_lists_are_read(tmp_path):
    """THE regression. `out[email] = {...}` per file meant the LAST list read silently
    overwrote every earlier one, so which sequence an address was attributed to depended on
    `m["messages"]` order. Reversing that order on a live profile moved sequence
    `Mgw47XpZzA` from "41 admissible against 45 enrolled" to "0 against 45" — a five-alarm
    drift warning manufactured out of iteration order, on the panel whose job is to report
    drift honestly.
    """
    m = _two_list_model(tmp_path)
    forward = _staged_candidates(m)
    reversed_ = _staged_candidates({**m, "messages": list(reversed(m["messages"]))})
    assert forward == reversed_, "candidate map must be a property of the files, not their order"
    assert forward["both@northgate.example"]["sequences"] == ("seqA", "seqB")


def test_an_address_on_two_lists_is_admissible_work_on_both(tmp_path):
    """Positive control: it is not enough for the map to be stable — it must carry BOTH
    memberships. A stable map that still kept one label would pass the test above."""
    c = _staged_candidates(_two_list_model(tmp_path))
    assert len(c["both@northgate.example"]["sequences"]) == 2
    assert c["onlya@ashfield.example"]["sequences"] == ("seqA",)
    assert c["onlyb@quaymark.example"]["sequences"] == ("seqB",)


def test_reconcile_counts_a_shared_address_under_every_sequence_that_lists_it(tmp_path):
    """`admissible on the list` is a fact about a (row, sequence) PAIR, which is what the
    provider's own per-sequence enrolled count is counting on the other side. Attributing a
    shared address to one sequence left the other short against a provider number that
    included it — drift on the page, arithmetic in the code."""
    m = _two_list_model(tmp_path)
    m["campaigns"] = {
        "campaigns": [
            {
                "sequences": [
                    {"sequence_id": "seqA", "enrolled": 2},
                    {"sequence_id": "seqB", "enrolled": 2},
                ]
            }
        ]
    }
    candidates = _staged_candidates(m)
    staged = [
        _row("Northgate", email="both@northgate.example", verdict="send"),
        _row("Ashfield", email="onlya@ashfield.example", verdict="send"),
        _row("Quaymark", email="onlyb@quaymark.example", verdict="send"),
    ]
    html = _reconcile(m, staged, candidates)
    # Each sequence lists 2 admissible rows (the shared one plus its own) and the provider
    # reports 2 — so both must reconcile. Counting the shared row once would read 1 vs 2.
    assert "they agree" in html
    assert "but 2 enrolled" not in html, (
        f"a shared address was counted under only one sequence:\n{html}"
    )
