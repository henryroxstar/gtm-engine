"""Tests for gtm_core.sequencer_outcomes.

Payload fixtures are trimmed copies of real Saleshandy responses captured 2026-08-18,
so the mapping is tested against the shape the provider actually returns rather than
the shape the design assumed (the two differed in three ways — see the module docstring).
Identities are fictional per the third-party-PII rule; only the field structure is real.
"""

from __future__ import annotations

import json

from gtm_core import sequencer_outcomes as so

TAXONOMY = {
    "payload": {
        "items": [
            {"id": "E9K", "name": "Uncategorized", "isDefault": True, "sentiment": "Neutral"},
            {"id": "rBV", "name": "Interested", "isDefault": True, "sentiment": "Positive"},
            {"id": "r1q", "name": "Not Interested", "isDefault": True, "sentiment": "Negative"},
            {"id": "ZMg", "name": "Meeting Booked", "isDefault": True, "sentiment": "Positive"},
            {"id": "8gl", "name": "Out of Office", "isDefault": True, "sentiment": "Neutral"},
            {"id": "LY8", "name": "Closed", "isDefault": True, "sentiment": "Positive"},
            {"id": "n6v", "name": "Not Now", "isDefault": True, "sentiment": "Negative"},
            {"id": "edl", "name": "Do Not Contact", "isDefault": True, "sentiment": "Negative"},
        ]
    }
}

OURS = "rep@ourco.example"
THEIRS = "dana.quill@northwind.example"


def _email_item(**over):
    base = {
        "emailThreadId": "T1",
        "sequenceId": "SEQ1",
        "categoryId": 2,
        "sentiment": "Positive",
        "isRepliedByProspect": 1,
        "subject": "Re: your agents in production",
        "sentAt": "2026-08-11T14:34:43.000Z",
        "prospectName": "Dana Quill",
        "isUnsubscribed": 0,
    }
    base.update(over)
    return {"payload": {"items": [base]}}


def _thread(tid="T1", prospect=THEIRS):
    """Two-message thread in the provider's real shape: the prospect side of each
    message is labelled with the same prospect id, ours is left null."""
    return {
        "emailThreadId": tid,
        "payload": [
            {
                "emailId": "a",
                "fromEmail": OURS,
                "to": [prospect],
                "fromProspectId": None,
                "toProspectId": 209175356,
            },
            {
                "emailId": "b",
                "fromEmail": prospect,
                "to": [OURS],
                "fromProspectId": 209175356,
                "toProspectId": None,
            },
        ],
    }


CELLS = {THEIRS: "enterprise:security:alpha"}


def test_positional_category_map_resolves_names():
    m = so.build_category_map(TAXONOMY)
    assert m[2]["name"] == "Interested"
    assert m[8] == {"name": "Do Not Contact", "sentiment": "Negative"}


def test_prospect_email_recovered_from_thread():
    assert so.prospect_email_by_thread([_thread()]) == {"T1": THEIRS}


def test_unlabelled_third_party_is_ignored_not_absorbed():
    """A CC'd address the provider does not label as a prospect is not the prospect —
    and must not be mistaken for our own mailbox either, which would hide it."""
    t = _thread()
    t["payload"].append(
        {
            "emailId": "c",
            "fromEmail": "assistant@northwind.example",
            "to": [OURS],
            "fromProspectId": None,
            "toProspectId": None,
        }
    )
    assert so.prospect_email_by_thread([t]) == {"T1": THEIRS}


def test_thread_naming_two_prospects_is_skipped_not_guessed():
    t = _thread()
    t["payload"].append(
        {
            "emailId": "c",
            "fromEmail": "someone.else@elsewhere.example",
            "to": [OURS],
            "fromProspectId": 111222333,
        }
    )
    assert so.prospect_email_by_thread([t]) == {}


def test_reply_maps_to_cell_tagged_rows():
    rows, unresolved = so.plan_rows(_email_item(), [_thread()], TAXONOMY, CELLS)
    assert unresolved == []
    reply = rows[0]
    assert reply["outcome"] == "reply"
    assert reply["ref"] == "T1"
    assert "cell:enterprise:security:alpha" in reply["tags"]
    assert "positive:interested" in reply["tags"]
    # Interested also emits a rate-bearing positive_reply so "replied" and
    # "replied and it was good news" stay separable.
    assert [r["outcome"] for r in rows] == ["reply", "positive_reply"]


def test_sentiment_disagreement_refuses_the_positional_guess():
    """categoryId is positional by inference; the item's own sentiment is the check."""
    rows, _ = so.plan_rows(
        _email_item(categoryId=2, sentiment="Negative"), [_thread()], TAXONOMY, CELLS
    )
    assert rows[0]["meta"]["category_resolved"] is False
    assert "unclassified" in rows[0]["tags"]
    assert not any(t.startswith("positive:") for t in rows[0]["tags"])


def test_non_reply_rows_are_ignored():
    rows, unresolved = so.plan_rows(
        _email_item(isRepliedByProspect=0), [_thread()], TAXONOMY, CELLS
    )
    assert rows == [] and unresolved == []


def test_unknown_address_is_unresolved_rather_than_dropped_silently():
    rows, unresolved = so.plan_rows(_email_item(), [_thread()], TAXONOMY, {})
    assert rows == []
    assert "not in any enrolment list" in unresolved[0]["reason"]


def test_missing_thread_payload_is_unresolved():
    rows, unresolved = so.plan_rows(_email_item(), [], TAXONOMY, CELLS)
    assert rows == []
    assert "no prospect address" in unresolved[0]["reason"]


def test_reconcile_appends_once_then_reclassifies():
    planned, _ = so.plan_rows(_email_item(), [_thread()], TAXONOMY, CELLS)

    # First run: nothing in the ledger, so the reply is new.
    new, reclass = so.reconcile(planned, [])
    assert len(new) == 2 and reclass == []

    # Second run, unchanged: no duplicate reply row, no re-classification.
    existing = [{"outcome": "reply", "ref": "T1", "tags": ["cell:x", "positive:interested"]}]
    new, reclass = so.reconcile(planned, existing)
    assert new == [] and reclass == []

    # Operator re-tags it in the inbox: a zero-valued classification row, not a
    # second reply — the reply count must not move.
    later, _ = so.plan_rows(
        _email_item(categoryId=3, sentiment="Negative"), [_thread()], TAXONOMY, CELLS
    )
    new, reclass = so.reconcile(later, existing)
    assert new == []
    assert len(reclass) == 1
    assert reclass[0]["outcome"] == so.CLASSIFIED
    assert reclass[0]["value"] == 0
    assert "objection:not-interested" in reclass[0]["tags"]
    assert reclass[0]["meta"]["supersedes_tag"] == "positive:interested"


def test_missing_custom_categories_reported():
    missing = so.missing_custom_categories(TAXONOMY)
    assert "Already solved (vendor)" in missing
    assert "Wrong person" in missing


def test_unattributed_replies_can_be_recorded_rather_than_dropped():
    """A reply that happened is a fact even when it cannot be attributed. Dropping it
    undercounts the reply total — the one number the run exists to measure."""
    emails = _email_item()
    # No cell map entry for this address.
    rows, unresolved = so.plan_rows(emails, [_thread()], TAXONOMY, {})
    assert rows == [] and len(unresolved) == 1

    rows, unresolved = so.plan_rows(emails, [_thread()], TAXONOMY, {}, include_unattributed=True)
    assert len(unresolved) == 1, "the gap is still reported, not hidden"
    assert f"cell:{so.UNATTRIBUTED}" in rows[0]["tags"]
    assert rows[0]["outcome"] == "reply"
    # It still carries its sequence and category, so it is not an anonymous tally.
    assert "seq:SEQ1" in rows[0]["tags"]
    assert "positive:interested" in rows[0]["tags"]


# --------------------------------------------------------- lane tag, recipient, opt-out (2026-09-03)


def test_reply_rows_carry_the_recipient_and_a_lane_tag():
    from gtm_core.sequencer_outcomes import plan_rows

    rows, _ = plan_rows(
        _email_item(), [_thread()], TAXONOMY, CELLS, email_to_lane={THEIRS: "personalised"}
    )
    assert rows[0]["meta"]["prospect_email"] == THEIRS
    assert "lane:personalised" in rows[0]["tags"]
    rows, _ = plan_rows(_email_item(), [_thread()], TAXONOMY, CELLS)
    assert not any(t.startswith("lane:") for t in rows[0]["tags"]), "no lane known → no lane tag"


def test_an_unsubscribe_emits_an_opt_out_row_that_is_not_a_reply():
    from gtm_core.outcomes import OPTOUT_OUTCOMES, REPLY_OUTCOMES
    from gtm_core.sequencer_outcomes import plan_rows

    rows, _ = plan_rows(_email_item(isUnsubscribed=1), [_thread()], TAXONOMY, CELLS)
    outcomes = [r["outcome"] for r in rows]
    assert "opt_out" in outcomes and outcomes.count("reply") == 1
    assert not OPTOUT_OUTCOMES & REPLY_OUTCOMES
    rows, _ = plan_rows(
        _email_item(categoryId=8, sentiment="Negative"), [_thread()], TAXONOMY, CELLS
    )
    assert "opt_out" in [r["outcome"] for r in rows], "a do-not-contact category is an opt-out"


# --- PS6: reply_emails_to_mark — the boundary that decides who gets mark_replied ---- #


def test_a_positive_interested_outcome_is_marked_for_replied():
    rows, _ = so.plan_rows(_email_item(), [_thread()], TAXONOMY, CELLS)
    assert [r["outcome"] for r in rows] == ["reply", "positive_reply"]
    assert so.reply_emails_to_mark(rows) == {THEIRS}


def test_a_meeting_outcome_is_marked_for_replied():
    # categoryId=4 -> "Meeting Booked" in TAXONOMY's 1-based positional map.
    rows, _ = so.plan_rows(
        _email_item(categoryId=4, sentiment="Positive"), [_thread()], TAXONOMY, CELLS
    )
    assert "meeting" in [r["outcome"] for r in rows]
    assert so.reply_emails_to_mark(rows) == {THEIRS}


def test_an_opt_out_outcome_is_not_marked_for_replied():
    """A do-not-contact category is an opt-out, never a positive reply — marking it
    'replied' would be the opposite signal from what actually happened."""
    rows, _ = so.plan_rows(
        _email_item(categoryId=8, sentiment="Negative"), [_thread()], TAXONOMY, CELLS
    )
    assert "opt_out" in [r["outcome"] for r in rows]
    assert so.reply_emails_to_mark(rows) == set()


def test_a_bare_reply_with_no_positive_tag_is_not_marked_for_replied():
    """An unresolved/unclassified reply — no 'positive:' tag at all — must not be
    marked either. Only positive_reply/meeting count."""
    rows, _ = so.plan_rows(
        _email_item(categoryId=1, sentiment="Neutral"), [_thread()], TAXONOMY, CELLS
    )
    assert [r["outcome"] for r in rows] == ["reply"]
    assert so.reply_emails_to_mark(rows) == set()


def test_reply_emails_to_mark_is_empty_for_no_rows():
    assert so.reply_emails_to_mark([]) == set()


# --- PS6: the CLI's --apply path actually calls mark_replied ---------------------- #


def test_cli_apply_calls_mark_replied_for_a_positive_reply(tmp_path, monkeypatch):
    """Wiring proof: a positive reply staged end-to-end through the CLI's --apply path
    reaches `mark_replied`. `--include-unattributed` sidesteps needing a real
    `cells.toml` for this test — the cell/lane join is exercised elsewhere."""
    emails_path = tmp_path / "emails.json"
    threads_path = tmp_path / "threads.json"
    taxonomy_path = tmp_path / "taxonomy.json"
    emails_path.write_text(json.dumps(_email_item()), encoding="utf-8")
    threads_path.write_text(json.dumps([_thread()]), encoding="utf-8")
    taxonomy_path.write_text(json.dumps(TAXONOMY), encoding="utf-8")

    calls = []

    def fake_mark_replied(profile, emails, *, source, content_root=None):
        calls.append((profile, set(emails), source, content_root))
        return {"changed": 1, "retired_skipped": [], "unmatched": []}

    monkeypatch.setattr(so, "mark_replied", fake_mark_replied)

    so._cli(
        [
            "--profile",
            "acme",
            "--content-root",
            str(tmp_path),
            "--emails",
            str(emails_path),
            "--threads",
            str(threads_path),
            "--taxonomy",
            str(taxonomy_path),
            "--include-unattributed",
            "--apply",
        ]
    )
    assert calls == [("acme", {THEIRS}, "sequencer_outcomes", tmp_path)]


def test_cli_apply_does_not_call_mark_replied_when_no_positive_reply(tmp_path, monkeypatch):
    emails_path = tmp_path / "emails.json"
    threads_path = tmp_path / "threads.json"
    taxonomy_path = tmp_path / "taxonomy.json"
    # Not Interested (categoryId=3) — a reply, but never a positive one.
    emails_path.write_text(
        json.dumps(_email_item(categoryId=3, sentiment="Negative")), encoding="utf-8"
    )
    threads_path.write_text(json.dumps([_thread()]), encoding="utf-8")
    taxonomy_path.write_text(json.dumps(TAXONOMY), encoding="utf-8")

    calls = []
    monkeypatch.setattr(
        so, "mark_replied", lambda *a, **kw: calls.append((a, kw)) or {"changed": 0}
    )

    so._cli(
        [
            "--profile",
            "acme",
            "--content-root",
            str(tmp_path),
            "--emails",
            str(emails_path),
            "--threads",
            str(threads_path),
            "--taxonomy",
            str(taxonomy_path),
            "--include-unattributed",
            "--apply",
        ]
    )
    assert calls == []


def test_cli_apply_retries_prior_unmarked_outcomes(tmp_path, monkeypatch):
    """PS-R C2: durable outcomes in outcomes.jsonl are retried on each --apply run."""
    from gtm_core.outcomes import append_outcome

    append_outcome(
        tmp_path,
        "acme",
        {
            "outcome": "positive_reply",
            "meta": {"prospect_email": "prior@target.example"},
        },
    )

    emails_path = tmp_path / "emails.json"
    threads_path = tmp_path / "threads.json"
    taxonomy_path = tmp_path / "taxonomy.json"
    emails_path.write_text(
        json.dumps(_email_item(categoryId=3, sentiment="Negative")), encoding="utf-8"
    )
    threads_path.write_text(json.dumps([_thread()]), encoding="utf-8")
    taxonomy_path.write_text(json.dumps(TAXONOMY), encoding="utf-8")

    calls = []

    def fake_mark_replied(profile, emails, *, source, content_root=None):
        calls.append((profile, set(emails), source, content_root))
        return {"changed": 1, "retired_skipped": [], "unmatched": []}

    monkeypatch.setattr(so, "mark_replied", fake_mark_replied)

    so._cli(
        [
            "--profile",
            "acme",
            "--content-root",
            str(tmp_path),
            "--emails",
            str(emails_path),
            "--threads",
            str(threads_path),
            "--taxonomy",
            str(taxonomy_path),
            "--include-unattributed",
            "--apply",
        ]
    )
    assert calls == [("acme", {"prior@target.example"}, "sequencer_outcomes", tmp_path)]
