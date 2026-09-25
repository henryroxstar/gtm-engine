"""Tests for gtm_core.optout_sets — the shared address key and per-event opt-out sets.

`optout_sets` is deliberately the ONLY thing shared between the Results page's people-counts
(campaign-page restructure, Phase 3 item 2) and
`agent.dnc_dispatch.open_candidates`'s gated-DNC-add vocabulary: the address key and the three
per-event sets, never a formula. Most of this file pins the sets' behaviour on their own; the
final test is the AGREEMENT property the test plan requires (T3.2): `open_candidates`,
computed by the dispatcher over a fake ledgers object, still equals
`(detected | unreadable) - added` computed by `optout_sets` from the exact same rows.
"""

from __future__ import annotations

from agent.dnc_dispatch import open_candidates
from gtm_core.optout_sets import optout_key, optout_sets


def _row(event: str, email: object) -> dict:
    return {"event": event, "email": email}


class _FakeLedgers:
    """The one surface `open_candidates` reads off a ledgers object: `iter_history`."""

    def __init__(self, rows):
        self._rows = list(rows)

    def iter_history(self):
        return iter(self._rows)


def test_optout_key_trims_and_lowercases():
    assert optout_key("  Sam@Example.COM ") == "sam@example.com"
    assert optout_key(None) == ""
    assert optout_key("") == ""


def test_case_and_whitespace_variants_of_one_address_count_once():
    rows = [
        _row("optout_detected", "Riley.Chen@example.com"),
        _row("optout_detected", "  riley.chen@example.com  "),
        _row("optout_detected", "RILEY.CHEN@EXAMPLE.COM"),
    ]
    detected, unreadable, added, unattributable = optout_sets(rows)
    assert detected == {"riley.chen@example.com"}
    assert unreadable == set()
    assert added == set()
    assert unattributable == 0


def test_a_re_recorded_row_counts_once():
    """A sweep that was killed and re-run writes the same optout_detected row twice."""
    rows = [_row("optout_detected", "jordan@example.com")] * 2
    detected, _unreadable, _added, _unattributable = optout_sets(rows)
    assert detected == {"jordan@example.com"}


def test_dnc_added_for_one_of_two_opted_out_addresses():
    rows = [
        _row("optout_detected", "avery@example.com"),
        _row("optout_detected", "morgan@example.com"),
        _row("dnc_added", "avery@example.com"),
    ]
    detected, _unreadable, added, _unattributable = optout_sets(rows)
    assert len(detected) == 2
    assert len(detected & added) == 1


def test_an_optout_unreadable_row_lands_in_unreadable_not_detected():
    rows = [_row("optout_unreadable", "quinn@example.com")]
    detected, unreadable, _added, _unattributable = optout_sets(rows)
    assert unreadable == {"quinn@example.com"}
    assert detected == set()


def test_a_row_with_no_address_is_unattributable_never_dropped():
    """Empty string, None, and a missing `email` key all normalise to the same empty
    key — each still increments `unattributable` rather than vanishing, and this holds
    across all three tracked events, not just `optout_detected`."""
    rows = [
        _row("optout_detected", ""),
        _row("optout_unreadable", None),
        {"event": "dnc_added"},  # no "email" key at all
    ]
    detected, unreadable, added, unattributable = optout_sets(rows)
    assert detected == set()
    assert unreadable == set()
    assert added == set()
    assert unattributable == 3


def test_unrelated_events_are_ignored():
    rows = [
        _row("signal", "casey@example.com"),
        _row("published", "drew@example.com"),
        _row("signal", ""),  # unrelated event, no address — still not unattributable
        {"event": "published"},  # unrelated event, no "email" key at all — same
    ]
    detected, unreadable, added, unattributable = optout_sets(rows)
    assert detected == set()
    assert unreadable == set()
    assert added == set()
    assert unattributable == 0


def test_open_candidates_matches_head_on_a_malformed_event_type():
    """CRITICAL regression pin. HEAD's `open_candidates` computed the address BEFORE
    touching `event`, so a row with an empty/absent address never tests `event` for set
    membership at all — even when `event` decoded to an unhashable JSON value (a list or
    dict), which a corrupt or adversarial ledger line can produce. `optout_sets` must
    preserve that order: an empty-key row is skipped before `event in _TRACKED_EVENTS` is
    evaluated, so a list- or dict-valued `event` paired with an empty/absent address does
    not raise `TypeError: unhashable type` where HEAD silently skipped the row."""
    rows = [
        {"event": "optout_detected", "email": "avery@example.com"},
        {"event": ["optout_detected"], "email": ""},  # unhashable event, empty address
        {"event": {}},  # unhashable event, no "email" key at all
    ]
    assert open_candidates(_FakeLedgers(rows)) == {"avery@example.com"}


def test_agreement_open_candidates_equals_detected_or_unreadable_minus_added():
    """T3.2's shared-sets property: on one fixture of mixed rows, `open_candidates`
    (agent/dnc_dispatch.py, the gated DNC-add path) equals `(detected | unreadable) -
    added` computed by `optout_sets` from the same rows — proving the dispatcher's
    formula and the page's counts read the same underlying partition.

    Two rows exist only to kill mutants a smaller fixture would miss. `sky@example.com`
    has a `dnc_added` row and NO opt-out row at all — it must land in neither `detected`
    nor `open_candidates`; a `^` (symmetric difference) in place of `-` would wrongly
    admit it, and so would a bug that files a `dnc_added` row into `detected` too.
    `drew@example.com` is a plain `optout_detected` address that is never added, so the
    fixture has at least one member that survives the subtraction on its own, not only
    riding along with `avery`'s (detected-and-added) or `blair`'s (unreadable-only) path.
    """
    rows = [
        _row("optout_detected", "avery@example.com"),
        _row("optout_detected", "Avery@Example.com"),  # same person, different case
        _row("optout_unreadable", "blair@example.com"),
        _row("optout_detected", "drew@example.com"),  # detected, never added
        _row("dnc_added", "avery@example.com"),
        _row("dnc_added", "sky@example.com"),  # added-only — no opt-out row at all
        _row("optout_detected", ""),  # unattributable — must not leak into either set
        _row("signal", "casey@example.com"),  # unrelated — ignored
    ]
    detected, unreadable, added, unattributable = optout_sets(rows)
    expected = (detected | unreadable) - added
    candidates = open_candidates(_FakeLedgers(rows))

    assert candidates == expected
    # Pin the fixture's own arithmetic so the property above can't pass vacuously.
    assert expected == {"blair@example.com", "drew@example.com"}
    assert unattributable == 1
    assert "sky@example.com" not in detected
    assert "sky@example.com" not in candidates
