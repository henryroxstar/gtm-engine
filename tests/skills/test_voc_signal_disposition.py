"""Contract: a signal's `disposition` records what a reader should DO NEXT, and says enough to act.

The first attempt at this field used `confirmed` / `unconfirmed-lead` / `do-not-use` and failed three
ways at once, all of which these tests now pin against:

1. `confirmed` was just `verified: true` restated, so the field advertised information it never
   carried. There is no such value now — an ordinary verified claim has an EMPTY disposition.
2. `unconfirmed-lead` hid three unrelated situations — no primary exists, not published yet, primary
   exists but blocked and we chose not to pay — so a reader had to ask a human which applied. `open`
   therefore REQUIRES `settled_by`: name the document, or do not use the label.
3. There was no slot for a claim a better source had corrected. That case was real (trade press said a
   round was "led by" an investor whose own site said "joining as a strategic investor"), so
   `superseded` exists and requires `superseded_by`.

Also pins the `customer_moves` / `account-event` lane, whose reason for existing is that
`funding_and_ma` iterates the competitor registry and can therefore only ever find rivals.
"""

from __future__ import annotations

import pytest

from gtm_core.voc import collect as voc
from gtm_core.voc import signals as sig
from gtm_core.voc import watermark as wm


def _raw(**overrides) -> dict:
    base = {
        "id": "sig-2026-07-29-example",
        "title": "Example signal",
        "date": "2026-07-29",
        "lane": "funding_and_ma",
        "speaker": "vendor-voice",
        "entity": "Example Inc",
        "url": "https://example.test/news",
        "direction": "neutral",
    }
    base.update(overrides)
    return base


# --- the ordinary case carries no annotation at all ------------------------------------------


def test_verified_claim_needs_no_disposition():
    """The replaced scheme forced a redundant `confirmed` here. Silence is the default now."""
    s = sig.from_dict(_raw(verified=True))
    assert s.disposition == sig.DISPOSITION_NONE
    assert s.citable is True


def test_there_is_no_confirmed_value_anymore():
    """`confirmed` restated `verified: true`; reintroducing it would restore the confusion."""
    assert "confirmed" not in sig.VALID_DISPOSITIONS
    with pytest.raises(sig.SignalValidationError):
        sig.from_dict(_raw(verified=True, disposition="confirmed"))


# --- an unusable claim must say WHICH kind of unusable ----------------------------------------


def test_unverified_claim_must_declare_open_or_refuted():
    """A blank disposition on an unverified claim is how a retraction and a to-do became
    indistinguishable in the first place."""
    with pytest.raises(sig.SignalValidationError) as exc:
        sig.from_dict(_raw(verified=False))
    assert "requires a disposition" in str(exc.value)


def test_open_requires_naming_the_document_that_would_settle_it():
    """'unconfirmed' is not actionable; 'read the FTC press release' is. Enforce the difference."""
    with pytest.raises(sig.SignalValidationError) as exc:
        sig.from_dict(_raw(verified=False, disposition="open"))
    assert "settled_by" in str(exc.value)

    s = sig.from_dict(
        _raw(
            verified=False,
            disposition="open",
            settled_by="FTC press release for the Growth Cave order (ftc.gov 403s free fetch)",
        )
    )
    assert s.citable is False


def test_refuted_requires_stating_what_was_checked():
    """Without this, the next reader repeats the search we already paid for."""
    with pytest.raises(sig.SignalValidationError) as exc:
        sig.from_dict(_raw(verified=False, disposition="refuted"))
    assert "checked" in str(exc.value)

    s = sig.from_dict(
        _raw(
            verified=False,
            disposition="refuted",
            checked="two searches of FTC actions and legal press; no such $150M action exists",
        )
    )
    assert s.citable is False


def test_superseded_requires_naming_the_better_source():
    with pytest.raises(sig.SignalValidationError) as exc:
        sig.from_dict(_raw(verified=True, disposition="superseded"))
    assert "superseded_by" in str(exc.value)


def test_superseded_is_verified_but_still_not_citable():
    """What was verified is that the claim AS STATED is wrong. The correction is citable; this
    record is not — the Akamai 'led by' vs 'strategic investor' case."""
    s = sig.from_dict(
        _raw(
            verified=True,
            disposition="superseded",
            superseded_by="hush.security/blog — 'Akamai joining as a strategic investor'; no lead named",
        )
    )
    assert s.verified is True
    assert s.citable is False


# --- annotations cannot contradict each other -------------------------------------------------


def test_open_contradicts_verified():
    with pytest.raises(sig.SignalValidationError) as exc:
        sig.from_dict(_raw(verified=True, disposition="open", settled_by="something"))
    assert "contradicts verified=True" in str(exc.value)


def test_a_stale_companion_field_is_rejected():
    """Flipping `open` to `refuted` while leaving `settled_by` behind would make the record say
    both 'go read X' and 'there is nothing to read'."""
    with pytest.raises(sig.SignalValidationError) as exc:
        sig.from_dict(
            _raw(
                verified=False,
                disposition="refuted",
                checked="two searches, nothing found",
                settled_by="the FTC press release",
            )
        )
    assert "settled_by" in str(exc.value)


def test_every_non_empty_disposition_requires_a_companion():
    """Pin the mapping itself, so adding a value without an actionable field is a test failure."""
    assert set(sig.DISPOSITION_REQUIRES) == sig.VALID_DISPOSITIONS - {sig.DISPOSITION_NONE}


def test_annotations_survive_a_round_trip():
    s = sig.from_dict(_raw(verified=False, disposition="refuted", checked="two searches"))
    again = sig.from_dict(s.to_dict())
    assert (again.disposition, again.checked) == ("refuted", "two searches")


# --- the customer_moves lane ------------------------------------------------------------------


def test_customer_moves_lane_and_account_event_speaker_are_valid():
    s = sig.from_dict(_raw(lane="customer_moves", speaker="account-event", verified=True))
    assert s.lane == "customer_moves"
    assert s.speaker == voc.ACCOUNT_EVENT


def test_account_event_never_counts_toward_demand_breadth():
    """The point of a separate speaker: a prospect's press release is not demand."""
    assert voc.counts_toward_breadth(voc.ACCOUNT_EVENT) is False
    assert voc.ACCOUNT_EVENT not in voc.BREADTH_ELIGIBLE_SPEAKERS


def test_customer_moves_has_its_own_watermark_lane():
    pol = wm.policy("customer_moves")
    assert pol.source_id == "customer_moves"
    assert pol.min_days == 30, "funding signal is lumpy; a short window reads as 'accounts quiet'"


def test_customer_moves_is_separate_from_the_competitor_funding_lane():
    """These must not collapse: one iterates the registry (rivals), one reads our accounts."""
    assert wm.policy("customer_moves").source_id != wm.policy("funding_and_ma").source_id
