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
    assert LABELS["ready_to_send"] == "Sorted — not yet checked"
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


# --- the count is DERIVED by the check report (PS15, 2026-09-24) ---------------
#
# PH2 derived this count inside the status command (`_checked_count`), per lane, and kept
# only the number: a batch that failed its audit read 0, and so did a list nobody checked.
# PS15 moved the derivation into the check report, where it carries its reasons and every
# row's fate. The eight PH2 tests that lived here guarded real gate properties (laned audit,
# all-or-nothing per batch, suppressed rows excluded, absent is not zero, unreadable is
# refused); they are ported, not deleted, to `tests/unit/test_prospect_readiness.py`.
