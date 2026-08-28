"""Unit tests for the only component in the email-quality loop that mutates prospect state.

Every fixture here is fictional (§R9). Real people and real companies live in
`profiles/` and `content/`; a test that carries one puts it in the public repo, which has
already happened three times.

The bias throughout is toward proving the writeback DOESN'T act, because that is the
expensive direction. A missed disqualification costs one more email; an over-eager one
silently removes good accounts from the pipeline and nobody notices until the list is
short.
"""

from __future__ import annotations

import json

import pytest

from gtm_core.eval_calibration import Label
from gtm_core.eval_writeback import DISQUALIFY_CEILING, build_plan
from gtm_core.suppression import EVAL_DISQUALIFIED, EVAL_WRONG_PERSON

SPEC_SHA = "a" * 16
CSV_SHA = "b" * 16


def _label(row_id: str, **kw) -> Label:
    """A label with everything answered 'good' unless overridden."""
    base = {
        "row_id": row_id,
        "spec_sha256": SPEC_SHA,
        "csv_sha256": CSV_SHA,
        "send_it": True,
        "fact_creates_problem": True,
        "fact_supports_pitch": True,
        "frame_fits_seat": True,
        "right_person": True,
    }
    base.update(kw)
    return Label(**base)


def _bad_fit(row_id: str, **kw) -> Label:
    """A label that says the ACCOUNT is wrong.

    ``account_fit=False`` is the whole of it. The fact-scoped sub-checks are left answered
    'good' on purpose: a competitor can have a perfectly accurate, perfectly relevant
    opening fact, and the reason not to write to them has nothing to do with the fact.
    """
    return _label(row_id, send_it=False, account_fit=False, **kw)


INTERNAL = {
    "r1": {"row_id": "r1", "email": "chief@acme.example", "company": "Acme Systems"},
    "r2": {"row_id": "r2", "email": "chief@acme.example", "company": "Acme Systems"},
    "r3": {"row_id": "r3", "email": "lead@beta.example", "company": "Beta Works"},
    "r4": {"row_id": "r4", "email": "ops@gamma.example", "company": "Gamma Group"},
}


# ── R2: the join is an accumulate, never a dict comprehension ─────────────────


def test_two_touches_on_one_email_both_survive_the_join():
    """One address legitimately carries several labels — one per touch.

    A dict comprehension keyed on email keeps the LAST and silently drops the rest. That
    exact defect has landed three times in this workstream: it inverted a 70%-enterprise
    list to '60% startup', and it dropped 2 of 5 phrase issues. Here the consequence is
    worse — the touch carrying the disqualifying evidence is the one thrown away.
    """
    labels = [_label("r1"), _bad_fit("r2", note="they sell this, they do not buy it")]
    plan = build_plan(labels, INTERNAL, list_size=10)

    assert plan.labels_joined == 2, "a label was dropped in the join"
    assert len(plan.disqualifications) == 1, (
        "the disqualifying touch was lost — the join kept only one label per address"
    )
    assert plan.disqualifications[0].note == "they sell this, they do not buy it"


def test_case_and_whitespace_variants_of_one_email_merge_to_one_row():
    """Two spellings of one address must not produce two actions for one person."""
    internal = {
        "r1": {"row_id": "r1", "email": "Chief@Acme.Example ", "company": "Acme Systems"},
        "r2": {"row_id": "r2", "email": "chief@acme.example", "company": "Acme Systems"},
    }
    plan = build_plan([_bad_fit("r1"), _bad_fit("r2")], internal, list_size=10)
    assert len(plan.disqualifications) == 1, "case/whitespace variants split into two actions"
    assert plan.disqualifications[0].email == "chief@acme.example"


# ── R3: it must not act on rows it has no mandate over ────────────────────────


def test_a_row_with_no_disqualifying_label_is_untouched():
    """The positive control. A writeback that edits a row it shouldn't is the worst bug here."""
    plan = build_plan([_label("r1"), _label("r3")], INTERNAL, list_size=10)
    assert plan.actions == [], "writeback acted on rows whose labels said everything was fine"
    assert plan.labels_joined == 2, (
        "…and it did so while the join was working, so it is not a no-op"
    )


def test_send_it_false_alone_is_not_a_disqualification():
    """The most common label on the sheet must not disqualify the account.

    `send_it: N` usually means 'this copy is wrong', which the repair loop fixes. Reading
    it as 'this account is bad' would disqualify most of a healthy list on a copy defect.
    """
    labels = [_label("r1", send_it=False), _label("r3", send_it=False, frame_fits_seat=False)]
    plan = build_plan(labels, INTERNAL, list_size=10)
    assert plan.disqualifications == [], (
        "a bare send_it:N disqualified an account — this turns every copy defect into a "
        "permanent account exclusion"
    )


def test_three_false_fact_subchecks_without_an_account_answer_do_not_disqualify():
    """The regression. A weak-but-honest signal on a good account must survive.

    The shape that motivated the whole change (2026-08-21 eval): a hospital system whose
    opening fact said it was already centralising AI oversight and governance. That fact
    creates no problem we solve, does not support the pitch, and the operator would not send
    that email — three honest Falses, and the account is a perfectly good prospect. The
    operator's own note on the row was a copy complaint, not a fit complaint.

    The retired predicate read exactly this pattern as 'the account is bad'. It could never
    have done otherwise: all three of those sub-checks are scoped to the row's fact, so no
    combination of them expresses a judgment about the company. With no answer to the one
    account-scoped question, the correct output is NO durable action.
    """
    labels = [
        _label(
            "r1",
            send_it=False,
            fact_creates_problem=False,
            fact_supports_pitch=False,
            # account_fit deliberately unset -> None -> no account-level evidence
        )
    ]
    plan = build_plan(labels, INTERNAL, list_size=10)

    assert plan.labels_joined == 1, "the label never joined, so this proves nothing"
    assert plan.disqualifications == [], (
        "a good account was disqualified on three fact-scoped Falses — this is the exact "
        "false positive account_fit was added to remove"
    )
    assert plan.actions == []


def test_an_unanswered_account_question_never_disqualifies_even_with_everything_else_bad():
    """Fail-safe direction, stated as its own property.

    `None` means 'nobody was asked', not 'the account is bad'. Every label file written
    before the field existed loads this way, so this is also what makes the field shippable
    without migrating a single stored label.
    """
    labels = [
        _label(
            "r1",
            send_it=False,
            fact_creates_problem=False,
            fact_supports_pitch=False,
            frame_fits_seat=False,
            account_fit=None,
        )
    ]
    plan = build_plan(labels, INTERNAL, list_size=10)
    assert plan.disqualifications == [], "an unanswered account question disqualified an account"


def test_account_fit_false_disqualifies_on_its_own():
    """The positive control for the new key, with every fact-scoped sub-check answered good.

    A competitor's opening fact can be accurate, fresh and on-topic; the reason not to write
    to them is not about the fact at all. If this needed a supporting fact-scoped False, the
    predicate would still be measuring the wrong grain.
    """
    labels = [_label("r1", account_fit=False, note="they sell this, they do not buy it")]
    plan = build_plan(labels, INTERNAL, list_size=10)

    assert len(plan.disqualifications) == 1
    assert plan.disqualifications[0].reason == EVAL_DISQUALIFIED
    assert plan.disqualifications[0].email == "chief@acme.example"
    assert plan.disqualifications[0].note == "they sell this, they do not buy it"


def test_account_fit_true_spares_the_account_however_bad_the_email_is():
    """'The email is wrong, the company is fine' must be expressible and must be honoured."""
    labels = [
        _label(
            "r1",
            send_it=False,
            fact_creates_problem=False,
            fact_supports_pitch=False,
            frame_fits_seat=False,
            account_fit=True,
        )
    ]
    plan = build_plan(labels, INTERNAL, list_size=10)
    assert plan.disqualifications == [], (
        "the operator said the account was fine and it was disqualified anyway"
    )


def test_wrong_person_suppresses_the_address_and_spares_the_account():
    """Three grains. The company may be exactly right; only this individual is wrong."""
    plan = build_plan([_label("r1", right_person=False, send_it=False)], INTERNAL, list_size=10)
    assert len(plan.suppressions) == 1
    assert plan.suppressions[0].reason == EVAL_WRONG_PERSON
    assert plan.disqualifications == [], "a wrong-person label disqualified the whole company"


def test_an_injected_row_never_disqualifies_a_real_account():
    """A planted defect is evidence about the linter, never about the company."""
    labels = [_bad_fit("r1", injected=True, injected_rule="company-allcaps")]
    plan = build_plan(labels, INTERNAL, list_size=10)
    assert plan.actions == [], (
        "an account was disqualified for a defect this program injected into its own copy"
    )


def test_apply_refuses_above_the_twenty_percent_ceiling_without_force(tmp_path, monkeypatch):
    """A join defect that matches too broadly looks exactly like a devastating list."""
    from gtm_core import eval_writeback

    labels_path = tmp_path / "labels.jsonl"
    labels_path.write_text(
        "\n".join(json.dumps(_bad_fit(r).to_dict()) for r in ("r1", "r3", "r4")) + "\n",
        encoding="utf-8",
    )
    internal_path = tmp_path / "internal.jsonl"
    internal_path.write_text(
        "\n".join(json.dumps(v) for v in INTERNAL.values()) + "\n", encoding="utf-8"
    )
    # 3 disqualifications out of a 4-account list = 75%, far above the ceiling.
    monkeypatch.setattr(eval_writeback, "load_latest", lambda *a, **k: {"items": [{}] * 4})

    rc = eval_writeback.main(
        [
            "apply",
            "--profile",
            "example",
            "--labels",
            str(labels_path),
            "--internal",
            str(internal_path),
            "--ledger",
            str(tmp_path / "sup.csv"),
        ]
    )
    assert rc == 1, "apply blew through the disqualification ceiling"
    assert not (tmp_path / "sup.csv").exists(), "a refused apply still wrote to the ledger"


def test_plan_is_the_default_and_writes_nothing(tmp_path, monkeypatch):
    """Dry-run must stay the default, and must actually be dry."""
    from gtm_core import eval_writeback

    labels_path = tmp_path / "labels.jsonl"
    labels_path.write_text(json.dumps(_bad_fit("r1").to_dict()) + "\n", encoding="utf-8")
    internal_path = tmp_path / "internal.jsonl"
    internal_path.write_text(
        "\n".join(json.dumps(v) for v in INTERNAL.values()) + "\n", encoding="utf-8"
    )
    ledger = tmp_path / "sup.csv"
    monkeypatch.setattr(eval_writeback, "load_latest", lambda *a, **k: {"items": [{}] * 10})

    rc = eval_writeback.main(
        [
            "plan",
            "--profile",
            "example",
            "--labels",
            str(labels_path),
            "--internal",
            str(internal_path),
            "--ledger",
            str(ledger),
        ]
    )
    assert rc == 0
    assert not ledger.exists(), "`plan` wrote to the suppression ledger"


# ── R4: the disqualification must reach the durable half ──────────────────────


def test_an_eval_disqualification_reaches_the_suppression_ledger(tmp_path, monkeypatch):
    """latest.json alone is decoration — nothing in the send-list build reads its status."""
    from gtm_core import eval_writeback
    from gtm_core.suppression import load

    labels_path = tmp_path / "labels.jsonl"
    labels_path.write_text(json.dumps(_bad_fit("r1").to_dict()) + "\n", encoding="utf-8")
    internal_path = tmp_path / "internal.jsonl"
    internal_path.write_text(
        "\n".join(json.dumps(v) for v in INTERNAL.values()) + "\n", encoding="utf-8"
    )
    ledger = tmp_path / "sup.csv"
    monkeypatch.setattr(eval_writeback, "load_latest", lambda *a, **k: {"items": [{}] * 10})
    monkeypatch.setattr(
        eval_writeback,
        "set_status",
        lambda *a, **k: {"changed": 0, "unchanged": 0, "unmatched": [], "total": 10},
    )

    rc = eval_writeback.main(
        [
            "apply",
            "--profile",
            "example",
            "--labels",
            str(labels_path),
            "--internal",
            str(internal_path),
            "--ledger",
            str(ledger),
        ]
    )
    assert rc == 0
    entries = load(ledger)
    assert "chief@acme.example" in entries, (
        "the disqualification never reached the ledger — it will not survive a pool rebuild"
    )
    assert entries["chief@acme.example"].reason == EVAL_DISQUALIFIED


def test_apply_is_idempotent(tmp_path, monkeypatch):
    """Running twice must not duplicate ledger rows or re-report changes."""
    from gtm_core import eval_writeback
    from gtm_core.suppression import load

    labels_path = tmp_path / "labels.jsonl"
    labels_path.write_text(json.dumps(_bad_fit("r1").to_dict()) + "\n", encoding="utf-8")
    internal_path = tmp_path / "internal.jsonl"
    internal_path.write_text(
        "\n".join(json.dumps(v) for v in INTERNAL.values()) + "\n", encoding="utf-8"
    )
    ledger = tmp_path / "sup.csv"
    monkeypatch.setattr(eval_writeback, "load_latest", lambda *a, **k: {"items": [{}] * 10})
    monkeypatch.setattr(
        eval_writeback,
        "set_status",
        lambda *a, **k: {"changed": 0, "unchanged": 1, "unmatched": [], "total": 10},
    )

    argv = [
        "apply",
        "--profile",
        "example",
        "--labels",
        str(labels_path),
        "--internal",
        str(internal_path),
        "--ledger",
        str(ledger),
    ]
    eval_writeback.main(argv)
    first = ledger.read_text(encoding="utf-8")
    eval_writeback.main(argv)
    assert ledger.read_text(encoding="utf-8") == first, "the second apply appended a duplicate row"
    assert len(load(ledger)) == 1


def test_the_ledger_never_downgrades_an_existing_optout(tmp_path):
    """A real opt-out outranks a fit judgment and must survive a writeback untouched."""
    from gtm_core.suppression import Suppression, append, load

    ledger = tmp_path / "sup.csv"
    append(ledger, [Suppression(email="x@acme.example", reason="dnc-optout", date="2026-08-01")])
    added, skipped = append(
        ledger, [Suppression(email="x@acme.example", reason=EVAL_DISQUALIFIED, date="2026-08-22")]
    )
    assert (added, skipped) == (0, 1)
    assert load(ledger)["x@acme.example"].reason == "dnc-optout", (
        "an eval writeback overwrote a genuine opt-out with a weaker reason"
    )


# ── R10: a stale label describes copy that no longer exists ───────────────────


def test_a_label_whose_spec_hash_no_longer_matches_is_refused_not_applied():
    """A re-cut spec invalidates every row_id — applying the old label writes a judgment
    about copy that was never sent to a row that never received it."""
    stale = _bad_fit("r1")
    plan = build_plan([stale], INTERNAL, list_size=10, spec_sha256="different", csv_sha256=CSV_SHA)
    assert plan.actions == [], "a stale label was applied"
    assert plan.stale_row_ids == ["r1"]

    # Positive control: the same label applies cleanly when the fingerprint matches.
    fresh = build_plan([stale], INTERNAL, list_size=10, spec_sha256=SPEC_SHA, csv_sha256=CSV_SHA)
    assert len(fresh.disqualifications) == 1


def test_a_label_joining_no_row_is_counted_not_silently_dropped():
    """'0 disqualified' and 'the join matched nothing' must be distinguishable."""
    plan = build_plan([_bad_fit("nonexistent")], INTERNAL, list_size=10)
    assert plan.actions == []
    assert plan.unjoined_row_ids == ["nonexistent"], (
        "an unjoined label vanished — the summary now reads as a clean list"
    )


def test_the_ceiling_is_a_share_of_the_list_not_a_count():
    """Sanity on the guard's own arithmetic — a 20% ceiling on a 10-row list is 2."""
    plan = build_plan([_bad_fit("r1"), _bad_fit("r3")], INTERNAL, list_size=10)
    assert plan.share_disqualified == pytest.approx(0.2)
    assert plan.share_disqualified <= DISQUALIFY_CEILING


# --- the ledger the gates actually read (D3) -------------------------------- #


def test_apply_defaults_the_ledger_to_where_the_gates_enforce_it(tmp_path, monkeypatch):
    """The default ledger was one segment short of every reader, so a durable
    disqualification landed in a file nothing consulted."""
    from gtm_core import eval_writeback, prospect_paths

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    canonical = prospect_paths.suppression_ledger("example", content_root=tmp_path)
    stale = prospect_paths.prospects_dir("example", content_root=tmp_path) / "suppression.csv"

    labels_path = tmp_path / "labels.jsonl"
    labels_path.write_text(json.dumps(_bad_fit("r1").to_dict()) + "\n", encoding="utf-8")
    internal_path = tmp_path / "internal.jsonl"
    internal_path.write_text(
        "\n".join(json.dumps(v) for v in INTERNAL.values()) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(eval_writeback, "load_latest", lambda *a, **k: {"items": [{}] * 50})

    rc = eval_writeback.main(
        [
            "apply",
            "--profile",
            "example",
            "--labels",
            str(labels_path),
            "--internal",
            str(internal_path),
        ]
    )
    assert rc == 0
    assert canonical.exists(), f"ledger not written to {canonical}"
    assert not stale.exists(), "ledger written to the pre-fix path nothing reads"


def test_disqualification_keys_on_the_company_domain_not_the_address(tmp_path):
    """A freemail contact at a real account must key the ACCOUNT, not the mail provider."""
    internal = {
        "r9": {
            "row_id": "r9",
            "email": "someone@gmail.example",
            "company": "Northwind Systems",
            "company_domain": "northwind.example",
        }
    }
    plan = build_plan([_bad_fit("r9")], internal, list_size=50)
    (action,) = plan.disqualifications
    assert action.company_domain == "northwind.example"


def test_company_fallback_is_reachable_when_no_company_domain_is_known(tmp_path):
    """Parsing the address always produced a domain, so this branch was dead code."""
    internal = {
        "r9": {"row_id": "r9", "email": "someone@gmail.example", "company": "Northwind Systems"}
    }
    plan = build_plan([_bad_fit("r9")], internal, list_size=50)
    (action,) = plan.disqualifications
    assert action.company_domain == ""


def test_disqualification_prefers_the_stamped_account_id(tmp_path):
    """The id latest.json assigned cannot disagree with latest.json about the account."""
    internal = {
        "r9": {
            "row_id": "r9",
            "email": "someone@gmail.example",
            "company": "Northwind Systems",
            "company_domain": "northwind.example",
            "account_id": "a-0123456789",
        }
    }
    plan = build_plan([_bad_fit("r9")], internal, list_size=50)
    (action,) = plan.disqualifications
    assert action.account_id == "a-0123456789"
