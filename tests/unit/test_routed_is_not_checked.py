"""Routed and checked are two counts, and the page must never read as one (P6 item 3).

`ready_to_send` is the ROUTER's state: it answers "which email could this person get". The
checks that answer "may it go" have not run at the point this table is printed. Until
2026-09-23 it rendered as **"Ready to send"** beside **"nobody's — it is done"**, so an
operator was told no action remained and then met a refusal at enrollment — a negative
surprise by construction, on a live run where 415 routed sat against 91 checked.

The fix is not a softer word. It is naming the step the count belongs to, and putting the
other count on its own line under its own label so the two can never be read as one.
"""

from __future__ import annotations

from gtm_core import prospect_status_cli as cli
from gtm_core.prospect_status import CHECKED_LABEL, LABELS, NEXT_STEP


def test_the_routed_label_no_longer_claims_completion():
    assert LABELS["ready_to_send"] == "Routed — not yet checked"
    assert NEXT_STEP["ready_to_send"] == "the checks, then yours"


def test_the_old_wording_is_gone_from_every_rendered_line():
    """Both halves, together: a label that says "Ready to send" beside a next step that says
    "it is done" is the defect, and removing only one of them leaves it readable."""
    report = cli._format_report({"ready_to_send": 415}, 0, 0)
    assert "Ready to send" not in report
    assert "it is done" not in report
    assert "nobody's" not in report


def test_routed_and_checked_render_as_two_distinguishable_lines():
    """The 415-versus-91 case. These two counts must NOT agree, and that is the point —
    the test is that a reader can tell which is which."""
    report = cli._format_report({"ready_to_send": 415}, 0, 0, checked_count=91)
    routed = [ln for ln in report.splitlines() if ln.startswith(LABELS["ready_to_send"])]
    checked = [ln for ln in report.splitlines() if ln.startswith(CHECKED_LABEL)]
    assert len(routed) == 1 and len(checked) == 1
    assert "415" in routed[0] and "91" in checked[0]
    assert "415" not in checked[0] and "91" not in routed[0]


def test_no_label_is_shared_between_the_two_counts():
    """A shared label would put the defect back whatever the numbers say."""
    assert CHECKED_LABEL != LABELS["ready_to_send"]
    assert CHECKED_LABEL not in LABELS.values()


def test_the_checked_line_says_not_run_rather_than_zero():
    """Absent is not zero. "the checks refused everything" and "the checks have not seen
    this list" are different facts an operator acts on differently, and rendering the second
    as `0` is a claim that the first happened."""
    report = cli._format_report({"ready_to_send": 415}, 0, 0)
    checked = next(ln for ln in report.splitlines() if ln.startswith(CHECKED_LABEL))
    assert "not run yet" in checked
    assert " 0 " not in checked


def test_the_checked_count_is_not_added_into_the_contacts_total():
    """Same people, different step. Adding them would double-count every one of them."""
    without = cli._format_report({"ready_to_send": 10, "waiting_on_you": 5}, 0, 0)
    with_checked = cli._format_report(
        {"ready_to_send": 10, "waiting_on_you": 5}, 0, 0, checked_count=4
    )

    def total(report: str) -> str:
        lines = report.splitlines()
        return lines[lines.index(next(ln for ln in lines if "─" in ln)) + 1]

    assert total(without) == total(with_checked)
    assert "15" in total(without)


def test_the_block_still_carries_no_pipeline_vocabulary():
    """PS11. Everything this returns is pasted to a person."""
    from tests.lint import operator_vocabulary as ov

    report = cli._format_report({"ready_to_send": 415}, 3, 1, checked_count=91)
    assert ov._scan("report", report) == []


# --- the count is now DERIVED, per lane (PH2, 2026-09-24) ----------------------
#
# `checked_count` was a parameter nothing in production ever passed, so this line read
# "not run yet" permanently. A caption that is always true says nothing — and it said
# nothing on the exact run (415 routed / 91 checked) that motivated the split above.
#
# The derivation must be LANED. The deleted dashboard copy (PH1) filtered `lane="signal"`
# and then audited UNLANED, so a routed generic row failed on `signal-*` errors the gate
# itself waves through. `test_a_generic_lane_row_is_not_judged_by_signal_rules` below is
# the guard against that returning here.

import csv as _csv

import pytest

from tests.test_preflight_report import _HEADER, _dossier, _row


@pytest.fixture
def content_root(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    return tmp_path


def _stage(root, profile: str, rows: list[dict]):
    """Like `test_preflight_report._staged`, but carrying `lane` and `suppression`.

    Written out rather than reused deliberately: the shared `_HEADER` has NEITHER column,
    and `_staged` writes only the columns in it — so every "lane" assertion below would
    have been made against a blank lane, passing while testing nothing. (It did, on the
    first run of this file.) The header a fixture writes is part of what it proves.
    """
    seq = root / profile / "prospects" / "sequences"
    seq.mkdir(parents=True, exist_ok=True)
    header = [*_HEADER, "lane", "suppression"]
    out = seq / "ready-to-load.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = _csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in header})
    return out


def test_no_list_on_disk_is_unknown_not_zero(content_root):
    """`None`, not 0. The checks genuinely have not seen a list that does not exist, and
    a confident 0 would read as 'everything was rejected'."""
    assert cli._checked_count("acme") is None


def test_a_clean_send_row_is_counted(content_root):
    _stage(content_root, "acme", [_row(lane="signal")])
    _dossier(content_root, "acme")
    assert cli._checked_count("acme") == 1


def test_a_row_the_gate_would_refuse_is_not_counted(content_root):
    """`verdict=hold` never reaches enrollment, so it must not reach this count either."""
    _stage(content_root, "acme", [_row(lane="signal", verdict="hold")])
    _dossier(content_root, "acme")
    assert cli._checked_count("acme") == 0


def test_a_generic_lane_row_is_not_judged_by_signal_rules(content_root):
    """THE lane-awareness guard, and the reason PH1's copy was deleted rather than moved.

    The finding must be one that `GENERIC_LANE_ADVISORY` actually demotes, or the test
    passes whether or not the audit is laned. `no-dossier` is such a finding: an ERROR in
    the signal lane (so the batch fails and contributes 0) and advisory in the generic lane
    (so the row counts). An UNLANED audit reports the stricter answer for both — which is
    precisely the F-B defect PH1's dead copy carried.

    An earlier version of this test used a blank `signal_clause` and passed against an
    unlaned audit, proving nothing (§R18). Paired with the signal-lane case below so the
    two lanes are asserted to DISAGREE, which no unlaned implementation can satisfy.
    """
    _stage(content_root, "acme", [_row(lane="generic")])  # deliberately no dossier
    assert cli._checked_count("acme") == 1


def test_the_same_row_in_the_signal_lane_is_refused(content_root):
    """The other half of the pair above. Same row, same missing dossier, different lane,
    opposite answer — that disagreement IS lane-awareness."""
    _stage(content_root, "acme", [_row(lane="signal")])  # deliberately no dossier
    assert cli._checked_count("acme") == 0


def test_a_blocked_lane_contributes_zero_not_its_candidates(content_root):
    """All-or-nothing per lane. The label says "these are the ones that may go"; nothing
    in a batch whose audit failed may go, so reporting its candidate count would be a
    send-authorisation claim the gate does not make."""
    _stage(content_root, "acme", [_row(lane="signal")])  # no dossier written
    assert cli._checked_count("acme") == 0


def test_a_suppressed_row_is_excluded(content_root):
    _stage(content_root, "acme", [_row(lane="signal", suppression="opted-out")])
    _dossier(content_root, "acme")
    assert cli._checked_count("acme") == 0


def test_an_unreadable_list_refuses_rather_than_undercounts(content_root, monkeypatch):
    """A smaller number and a correct one look identical. Refuse."""
    _stage(content_root, "acme", [_row(lane="signal")])
    _dossier(content_root, "acme")

    def _boom(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr("pathlib.Path.open", _boom)
    assert cli._checked_count("acme") is None
