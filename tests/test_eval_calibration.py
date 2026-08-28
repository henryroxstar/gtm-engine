"""Tests for the email eval-calibration harness — golden-set sampling/labels/holdout,
judge validation statistics, rule lifecycle audit, and outcome reconciliation.

All fixtures invented (docs/RULES.md R9). No real operator labels or send/reply data
exist yet — this suite proves the plumbing is correct against synthetic data shaped like
the real thing, which is exactly what the module is allowed to know before the operator's
own labeling session happens.
"""

from __future__ import annotations

import json
import os

import pytest

from gtm_core import eval_calibration as ec
from gtm_core.eval_calibration import (
    LABEL_FIELDS,
    ConfusionStats,
    Label,
    build_golden_row,
    cohens_kappa,
    confusion,
    evals_dir,
    flip_rate,
    judge_meets_bar,
    label_from_dict,
    labels_matching_fingerprint,
    read_labels,
    reconcile_outcomes,
    render_labeling_sheet,
    rule_fire_rates,
    rule_lifecycle_report,
    sample_golden_set,
    seal_holdout,
    validate_label_dict,
    write_labels,
)


def _label(row_id, send_it, **kw) -> Label:
    return Label(row_id=row_id, spec_sha256="a" * 16, csv_sha256="b" * 16, send_it=send_it, **kw)


# --------------------------------------------------------------------------- evals_dir


def test_evals_dir_is_content_root_profile_prospects_evals(tmp_path):
    out = evals_dir("acme", content_root=tmp_path)
    assert out == tmp_path / "acme" / "prospects" / "evals"


def test_evals_dir_rejects_traversal_profile(tmp_path):
    with pytest.raises(ValueError):
        evals_dir("../../etc", content_root=tmp_path)


# --------------------------------------------------------------------------- P0: golden set


def test_sample_golden_set_is_stable_across_runs():
    rows = [
        {
            "email": f"p{i}@x.example",
            "seat": "security" if i % 2 else "exec",
            "signal": "yes",
            "tier": "A",
        }
        for i in range(20)
    ]
    a = sample_golden_set(rows, n_real=8)
    b = sample_golden_set(rows, n_real=8)
    assert [r["email"] for r in a] == [r["email"] for r in b]
    assert len(a) == 8


def test_build_golden_row_id_is_stable_and_email_independent_of_case():
    a = build_golden_row(
        spec="s.md",
        csv="c.csv",
        touch=1,
        email="Chris@Example.com",
        subject="x",
        body="y",
        context={},
    )
    b = build_golden_row(
        spec="s.md",
        csv="c.csv",
        touch=1,
        email="chris@example.com",
        subject="different subject",
        body="different body",
        context={"title": "CISO"},
    )
    assert a.row_id == b.row_id  # id keys on spec/csv/email/touch only, not rendered content


def test_render_labeling_sheet_hides_injection_flag_and_rule():
    rows = [
        build_golden_row(
            spec="s.md",
            csv="c.csv",
            touch=1,
            email="a@x.example",
            subject="a subject",
            body="a body",
            context={"title": "CISO", "company": "Acme"},
            injected=True,
            injected_rule="signal-off-topic",
        )
    ]
    sheet = render_labeling_sheet(rows)
    assert "a subject" in sheet
    assert "a body" in sheet
    assert "CISO" in sheet and "Acme" in sheet
    assert "injected" not in sheet.lower()
    assert "signal-off-topic" not in sheet
    assert rows[0].row_id in sheet


def test_render_labeling_sheet_carries_no_email_address():
    rows = [
        build_golden_row(
            spec="s.md",
            csv="c.csv",
            touch=1,
            email="chris.renner@cascade.example",
            subject="x",
            body="y",
            context={},
        )
    ]
    sheet = render_labeling_sheet(rows)
    assert "chris.renner@cascade.example" not in sheet


# --------------------------------------------------------------------------- label I/O


def test_validate_label_dict_flags_missing_required_field():
    problems = validate_label_dict({"row_id": "x"})
    assert any("spec_sha256" in p for p in problems)
    assert any("csv_sha256" in p for p in problems)
    assert any("send_it" in p for p in problems)


def test_validate_label_dict_flags_wrong_type():
    problems = validate_label_dict(
        {"row_id": "x", "spec_sha256": "a", "csv_sha256": "b", "send_it": "yes"}
    )
    assert any("send_it must be boolean" in p for p in problems)


def test_validate_label_dict_allows_null_subchecks():
    problems = validate_label_dict(
        {
            "row_id": "x",
            "spec_sha256": "a",
            "csv_sha256": "b",
            "send_it": True,
            "fact_creates_problem": None,
        }
    )
    assert problems == []


def test_a_record_carrying_the_retired_refutes_field_is_rejected():
    """`fact_refutes_pitch` became `fact_supports_pitch` AND flipped polarity on 2026-08-21.

    It was the one sub-check where Y meant bad, so a labeler answering four questions in a
    row had to reverse polarity on the second. Rejecting the old key is the point of having
    renamed it rather than relabelling in place: `label_from_dict` reads sub-checks with
    `.get`, so a pre-rename record would otherwise load as "not answered" while its stored
    value means the OPPOSITE of the field that replaced it.
    """
    d = {
        "row_id": "x",
        "spec_sha256": "a",
        "csv_sha256": "b",
        "send_it": True,
        "fact_refutes_pitch": True,
    }
    problems = validate_label_dict(d)
    assert any("fact_refutes_pitch" in p and "fact_supports_pitch" in p for p in problems), problems
    with pytest.raises(ValueError, match="renamed"):
        label_from_dict(d)


def test_every_subcheck_reads_as_yes_is_good():
    """The consistency property the rename exists to create.

    Every sub-check states something the labeler AGREES with by answering Y, so Y is good on
    all of them and the answer column can be scanned without decoding each question.
    `account_fit` was added under the same constraint: Y means the account is fine.
    """
    assert LABEL_FIELDS == (
        "fact_creates_problem",
        "fact_supports_pitch",
        "frame_fits_seat",
        "right_person",
        "account_fit",
    )
    assert not any(f.startswith("fact_refutes") for f in LABEL_FIELDS)


def test_account_fit_is_the_only_account_scoped_subcheck():
    """The grain split the writeback depends on.

    `gtm_core.eval_writeback` disqualifies an account on `account_fit is False` and nothing
    else, precisely because every other sub-check is scoped to the row's own fact or copy.
    If a second account-scoped field is ever added, that predicate has to be revisited
    rather than silently left reading only one of them.
    """
    fact_or_copy_scoped = (
        "fact_creates_problem",
        "fact_supports_pitch",
        "frame_fits_seat",
        "right_person",
    )
    assert set(LABEL_FIELDS) - set(fact_or_copy_scoped) == {"account_fit"}


def test_account_fit_defaults_to_unanswered_and_survives_a_roundtrip():
    """Migration safety, as a property.

    A label file written before `account_fit` existed must load as None — 'nobody was
    asked' — never as an accusation about the account. `to_dict` must then carry the field
    so a re-serialised label does not lose an answer that WAS given.
    """
    legacy = {
        "row_id": "abc123",
        "spec_sha256": "a" * 16,
        "csv_sha256": "b" * 16,
        "send_it": False,
        "fact_creates_problem": False,
        "fact_supports_pitch": False,
    }
    assert label_from_dict(legacy).account_fit is None, (
        "a pre-field label loaded as something other than 'not answered'"
    )

    for answer in (True, False, None):
        rt = label_from_dict(label_from_dict({**legacy, "account_fit": answer}).to_dict())
        assert rt.account_fit is answer, f"account_fit={answer!r} did not survive a roundtrip"


def test_account_fit_must_be_boolean_or_null():
    """It rides the same validation loop as the other sub-checks, not a parallel one."""
    d = {
        "row_id": "abc123",
        "spec_sha256": "a" * 16,
        "csv_sha256": "b" * 16,
        "send_it": False,
        "account_fit": "no",
    }
    assert any("account_fit" in p for p in validate_label_dict(d))
    with pytest.raises(ValueError, match="account_fit"):
        label_from_dict(d)


def test_label_from_dict_roundtrip(tmp_path):
    d = {
        "row_id": "abc123",
        "spec_sha256": "a" * 16,
        "csv_sha256": "b" * 16,
        "send_it": False,
        "fact_creates_problem": False,
        "note": "the fact refutes the pitch",
    }
    label_ = label_from_dict(d)
    assert label_.send_it is False
    assert label_.fact_creates_problem is False
    assert label_.note == "the fact refutes the pitch"


def test_label_from_dict_raises_on_invalid():
    with pytest.raises(ValueError):
        label_from_dict({"row_id": "x"})


def test_write_and_read_labels_roundtrip(tmp_path):
    labels = [_label("r1", True), _label("r2", False, note="wrong seat")]
    path = write_labels(labels, tmp_path / "labels-2026-08-20.jsonl")
    back = read_labels(path)
    assert len(back) == 2
    assert {label_.row_id for label_ in back} == {"r1", "r2"}
    assert next(label_ for label_ in back if label_.row_id == "r2").note == "wrong seat"


def test_read_labels_skips_blank_lines(tmp_path):
    path = tmp_path / "labels.jsonl"
    path.write_text(
        json.dumps(_label("r1", True).to_dict())
        + "\n\n"
        + json.dumps(_label("r2", False).to_dict())
        + "\n",
        encoding="utf-8",
    )
    assert len(read_labels(path)) == 2


def test_labels_matching_fingerprint_excludes_stale():
    labels = [
        Label(row_id="r1", spec_sha256="a" * 16, csv_sha256="b" * 16, send_it=True),
        Label(row_id="r2", spec_sha256="STALE", csv_sha256="b" * 16, send_it=True),
    ]
    fresh = labels_matching_fingerprint(labels, spec_sha256="a" * 16, csv_sha256="b" * 16)
    assert [label_.row_id for label_ in fresh] == ["r1"]


# --------------------------------------------------------------------------- holdout sealing


def test_seal_holdout_is_class_balanced_and_stable():
    labels = [_label(f"pos{i}", True) for i in range(20)] + [
        _label(f"neg{i}", False) for i in range(20)
    ]
    train_a, holdout_a = seal_holdout(labels, n_per_class=5)
    train_b, holdout_b = seal_holdout(labels, n_per_class=5)
    assert {label_.row_id for label_ in holdout_a} == {label_.row_id for label_ in holdout_b}
    assert sum(1 for label_ in holdout_a if label_.send_it) == 5
    assert sum(1 for label_ in holdout_a if not label_.send_it) == 5
    assert len(train_a) + len(holdout_a) == len(labels)
    # train and holdout are disjoint
    assert not ({label_.row_id for label_ in train_a} & {label_.row_id for label_ in holdout_a})


def test_seal_holdout_raises_when_a_class_is_undersized():
    labels = [_label(f"pos{i}", True) for i in range(20)] + [_label("neg0", False)]
    with pytest.raises(ValueError):
        seal_holdout(labels, n_per_class=5)


# --------------------------------------------------------------------------- P1: confusion / kappa / flip


def test_confusion_positive_class_is_defect_caught():
    labels = [
        _label("r1", send_it=False),  # a real defect
        _label("r2", send_it=True),  # a clean row
    ]
    predictions = {"r1": False, "r2": True}  # judge agrees with both
    stats = confusion(labels, predictions)
    assert stats.tp == 1  # caught the defect
    assert stats.tn == 1  # passed the clean row
    assert stats.fp == 0
    assert stats.fn == 0
    assert stats.tpr == 1.0
    assert stats.tnr == 1.0


def test_confusion_counts_false_alarm_and_miss():
    labels = [_label("r1", send_it=False), _label("r2", send_it=True)]
    predictions = {"r1": True, "r2": False}  # judge disagrees with both
    stats = confusion(labels, predictions)
    assert stats.fn == 1  # missed the real defect (predicted sendable)
    assert stats.fp == 1  # false-alarmed on the clean row


def test_confusion_skips_unpredicted_rows():
    labels = [_label("r1", send_it=False), _label("r2", send_it=True)]
    stats = confusion(labels, {"r1": False})
    assert stats.n == 1


def test_cohens_kappa_perfect_agreement_is_one():
    labels = [_label(f"r{i}", send_it=(i % 2 == 0)) for i in range(10)]
    predictions = {label_.row_id: label_.send_it for label_ in labels}
    assert cohens_kappa(labels, predictions) == pytest.approx(1.0)


def test_cohens_kappa_chance_level_agreement_is_near_zero():
    # Judge predicts the MAJORITY class always — high raw agreement at a skewed base
    # rate, but kappa should correct for it and land near/below zero.
    labels = [_label(f"pos{i}", True) for i in range(9)] + [_label("neg0", False)]
    predictions = {label_.row_id: True for label_ in labels}  # always predicts sendable
    kappa = cohens_kappa(labels, predictions)
    assert kappa is not None
    assert kappa <= 0.05


def test_cohens_kappa_none_on_empty():
    assert cohens_kappa([], {}) is None


def test_flip_rate_all_stable_is_zero():
    assert flip_rate([(True, True), (False, False)]) == 0.0


def test_flip_rate_all_flipped_is_one():
    assert flip_rate([(True, False), (False, True)]) == 1.0


def test_flip_rate_empty_is_zero():
    assert flip_rate([]) == 0.0


def test_judge_meets_bar_passes_a_strong_judge():
    stats = ConfusionStats(tp=14, fp=2, tn=13, fn=1)
    verdict = judge_meets_bar(stats, kappa=0.75, flip=0.03)
    assert verdict.passed, verdict.reasons


def test_judge_meets_bar_fails_on_low_tpr():
    stats = ConfusionStats(tp=8, fp=1, tn=14, fn=7)  # TPR = 8/15 = 0.53
    verdict = judge_meets_bar(stats, kappa=0.8, flip=0.0)
    assert not verdict.passed
    assert any("TPR" in r for r in verdict.reasons)


def test_judge_meets_bar_fails_on_high_flip_rate():
    stats = ConfusionStats(tp=14, fp=1, tn=14, fn=1)
    verdict = judge_meets_bar(stats, kappa=0.9, flip=0.25)
    assert not verdict.passed
    assert any("flip" in r for r in verdict.reasons)


def test_judge_meets_bar_fails_when_undefined():
    stats = ConfusionStats(tp=0, fp=0, tn=10, fn=0)  # no positives at all -> TPR undefined
    verdict = judge_meets_bar(stats, kappa=0.9, flip=0.0)
    assert not verdict.passed
    assert any("TPR undefined" in r for r in verdict.reasons)


# --------------------------------------------------------------------------- P2: rule lifecycle


def _qa_record(renders, by_rule, checks_run):
    return {"renders": renders, "by_rule": by_rule, "checks_run": {r: {} for r in checks_run}}


def test_rule_fire_rates_aggregates_across_records():
    records = [
        _qa_record(100, {"specificity": {"WARN": 30}}, ["specificity", "greeting"]),
        _qa_record(
            50, {"specificity": {"WARN": 10}, "greeting": {"ERROR": 1}}, ["specificity", "greeting"]
        ),
    ]
    rates = rule_fire_rates(records)
    assert rates["specificity"] == (40, 150)
    assert rates["greeting"] == (1, 150)


def test_rule_lifecycle_recalibrate_band_for_saturated_rule():
    records = [_qa_record(100, {"specificity": {"WARN": 88}}, ["specificity"])]
    report = rule_lifecycle_report(records, labels=[])
    verdict = next(v for v in report if v.rule == "specificity")
    assert verdict.verdict == "recalibrate"


def test_rule_lifecycle_delete_candidate_when_zero_fires_across_enough_records():
    """Zero fires is a delete signal only once there is enough evidence to call it one."""
    records = [_qa_record(100, {}, ["banned-word"]), _qa_record(80, {}, ["banned-word"])]
    report = rule_lifecycle_report(records, labels=[])
    verdict = next(v for v in report if v.rule == "banned-word")
    assert verdict.verdict == "delete-candidate"


def test_zero_fires_on_a_single_record_is_unproven_not_useless():
    """The band that would have retired 62 of 69 rules on this report's first real run.

    Given one QA record from one clean list and no labels, every rule that list never
    exercised — no empty company, no trademark glyph, no role address — showed zero fires.
    Reporting absence of evidence as evidence of uselessness would delete the fleet on a
    healthy list, which is the confidently-wrong output this whole program exists to stop.
    """
    records = [_qa_record(100, {}, ["banned-word"])]
    report = rule_lifecycle_report(records, labels=[])
    verdict = next(v for v in report if v.rule == "banned-word")
    assert verdict.verdict == "insufficient-data"
    assert "unproven, not useless" in verdict.reason


def test_a_saturated_rule_is_still_flagged_on_a_single_record():
    """Positive control: the guard must not mute the OTHER bands.

    A rule firing on 60% of renders describes the list rather than screening it, and that
    is knowable from one record — the evidence threshold applies to the absence-based band
    only.
    """
    records = [_qa_record(100, {"specificity": {"WARN": 60}}, ["specificity"])]
    report = rule_lifecycle_report(records, labels=[])
    assert next(v for v in report if v.rule == "specificity").verdict == "recalibrate"


def test_rule_lifecycle_delete_candidate_when_injected_but_not_penalised():
    records = [_qa_record(100, {"signal-not-an-event": {"WARN": 20}}, ["signal-not-an-event"])]
    labels = [
        _label("r1", send_it=True, injected=True, injected_rule="signal-not-an-event"),
        _label("r2", send_it=True, injected=True, injected_rule="signal-not-an-event"),
    ]
    report = rule_lifecycle_report(records, labels)
    verdict = next(v for v in report if v.rule == "signal-not-an-event")
    assert verdict.verdict == "delete-candidate"
    assert verdict.injected_penalised == 0


def test_rule_lifecycle_keep_when_injected_instances_are_penalised():
    records = [_qa_record(100, {"email-role-address": {"ERROR": 5}}, ["email-role-address"])]
    labels = [
        _label("r1", send_it=False, injected=True, injected_rule="email-role-address"),
        _label("r2", send_it=False, injected=True, injected_rule="email-role-address"),
    ]
    report = rule_lifecycle_report(records, labels)
    verdict = next(v for v in report if v.rule == "email-role-address")
    assert verdict.verdict == "keep"
    assert verdict.injected_penalised == 2


def test_rule_lifecycle_no_control_when_never_measured():
    report = rule_lifecycle_report(qa_records=[], labels=[])
    assert report == []


def test_rule_lifecycle_reports_no_control_for_labeled_but_unrecorded_rule():
    labels = [_label("r1", send_it=False, injected=True, injected_rule="brand-new-rule")]
    report = rule_lifecycle_report(qa_records=[], labels=labels)
    verdict = next(v for v in report if v.rule == "brand-new-rule")
    # fire_rate is None (no QA record) but injected evidence exists and was penalised.
    assert verdict.fire_rate is None
    assert verdict.verdict == "keep"


# --------------------------------------------------------------------------- P3: outcome reconciliation


def test_reconcile_outcomes_below_floor_stays_labels_authoritative():
    judged = [
        {"email": "a@x.example", "judge_send_it": True, "stratum": "security"},
        {"email": "b@x.example", "judge_send_it": False, "stratum": "security"},
    ]
    outcomes = [{"channel": "email", "outcome": "reply", "ref": "a@x.example"}]
    result = reconcile_outcomes(judged, outcomes, min_events=5)
    row = next(r for r in result if r.stratum == "security")
    assert row.n_events == 1
    assert row.authoritative == "labels"


def test_reconcile_outcomes_above_floor_lets_outcomes_speak():
    judged = [
        {"email": f"pass{i}@x.example", "judge_send_it": True, "stratum": "exec"} for i in range(6)
    ] + [
        {"email": f"fail{i}@x.example", "judge_send_it": False, "stratum": "exec"} for i in range(6)
    ]
    outcomes = [
        {"channel": "email", "outcome": "reply", "ref": f"pass{i}@x.example"} for i in range(4)
    ] + [{"channel": "email", "outcome": "opt_out", "ref": f"fail{i}@x.example"} for i in range(3)]
    result = reconcile_outcomes(judged, outcomes, min_events=5)
    row = next(r for r in result if r.stratum == "exec")
    assert row.n_events == 7  # 4 pass-replies + 3 fail-optouts, all had SOME outcome
    assert row.authoritative == "outcomes"
    assert row.send_rate_judge_pass == pytest.approx(4 / 6)
    assert row.send_rate_judge_fail == pytest.approx(0 / 6)


def test_reconcile_outcomes_silence_is_not_a_label():
    # Every row here has NO outcome at all — silence is a real 0 in the RATE (a group
    # that hasn't replied yet genuinely has a 0% rate so far), but it must NOT be read as
    # a confirmed negative LABEL: n_events stays 0 and authoritative stays "labels", so
    # nothing downstream mistakes this 0.0 for a validated result.
    judged = [{"email": "a@x.example", "judge_send_it": True, "stratum": "startup"}]
    result = reconcile_outcomes(judged, outcome_rows=[], min_events=1)
    row = result[0]
    assert row.n_events == 0
    assert row.send_rate_judge_pass == 0.0
    assert row.authoritative == "labels"


def test_rule_lifecycle_not_human_visible_never_becomes_a_delete_candidate():
    """The guard added 2026-08-20 after a blind judge pass exposed the hole: three rules'
    defects can never appear on a labeling sheet (field absent from the copy, or the
    address withheld as PII). Without this band, 'fires but no injected instance was
    penalised' reads as 'nobody cared' and retires a working rule on an artifact of the
    sheet rather than a fact about the rule."""
    records = [_qa_record(100, {"email-domain-mismatch": {"WARN": 5}}, ["email-domain-mismatch"])]
    # An injected label exists and was NOT penalised — normally a delete-candidate.
    labels = [_label("r1", send_it=True, injected=True, injected_rule="email-domain-mismatch")]

    without_guard = rule_lifecycle_report(records, labels)
    assert (
        next(v for v in without_guard if v.rule == "email-domain-mismatch").verdict
        == "delete-candidate"
    )

    with_guard = rule_lifecycle_report(records, labels, not_human_visible=["email-domain-mismatch"])
    assert (
        next(v for v in with_guard if v.rule == "email-domain-mismatch").verdict
        == "not-human-visible"
    )


def test_rule_lifecycle_reports_invisible_rules_even_with_no_records_or_labels():
    report = rule_lifecycle_report([], [], not_human_visible=["last-name-symbols"])
    verdict = next(v for v in report if v.rule == "last-name-symbols")
    assert verdict.verdict == "not-human-visible"


# --------------------------------------------------------------------------- filled-sheet parser


def _sheet(row_id, answers, note=""):
    return f"""# Email eval — labeling sheet

## Row 1 — `{row_id}`

*title: CISO · company: Acme*

**Subject:** a subject

body text

{answers}

`note:` {note}

---
"""


def test_parse_filled_sheet_reads_answers_and_note():
    from gtm_core.eval_calibration import parse_filled_sheet

    text = _sheet(
        "a" * 16,
        "`send_it:` N  `fact_creates_problem:` N  `fact_supports_pitch:` Y  "
        "`frame_fits_seat:` Y  `right_person:` -",
        note="fact refutes the pitch",
    )
    labels = parse_filled_sheet(text, spec_sha256="s" * 16, csv_sha256="c" * 16)
    assert len(labels) == 1
    label_ = labels[0]
    assert label_.row_id == "a" * 16
    assert label_.send_it is False
    assert label_.fact_supports_pitch is True
    assert label_.right_person is None  # "-" means not applicable
    assert label_.note == "fact refutes the pitch"
    assert label_.spec_sha256 == "s" * 16


def test_parse_filled_sheet_accepts_case_and_word_variants():
    from gtm_core.eval_calibration import parse_filled_sheet

    for raw in ("Y", "y", "yes", "YES", "true", "1"):
        text = _sheet(
            "b" * 16,
            f"`send_it:` {raw}  `fact_creates_problem:` -  "
            "`fact_supports_pitch:` -  `frame_fits_seat:` -  `right_person:` -",
        )
        assert parse_filled_sheet(text)[0].send_it is True, raw
    for raw in ("N", "n", "no", "false", "0"):
        text = _sheet(
            "b" * 16,
            f"`send_it:` {raw}  `fact_creates_problem:` -  "
            "`fact_supports_pitch:` -  `frame_fits_seat:` -  `right_person:` -",
        )
        assert parse_filled_sheet(text)[0].send_it is False, raw


def test_parse_filled_sheet_skips_unlabeled_rows_but_keeps_labeled_ones():
    from gtm_core.eval_calibration import parse_filled_sheet

    text = _sheet(
        "a" * 16,
        "`send_it:` ___  `fact_creates_problem:` ___  `fact_supports_pitch:` ___  "
        "`frame_fits_seat:` ___  `right_person:` ___",
    ) + _sheet(
        "c" * 16,
        "`send_it:` Y  `fact_creates_problem:` Y  `fact_supports_pitch:` N  "
        "`frame_fits_seat:` Y  `right_person:` Y",
    )
    labels = parse_filled_sheet(text)
    assert [label_.row_id for label_ in labels] == ["c" * 16]  # blank row skipped, not errored


def test_parse_filled_sheet_raises_on_a_garbled_answer():
    from gtm_core.eval_calibration import parse_filled_sheet

    text = _sheet(
        "a" * 16,
        "`send_it:` maybe  `fact_creates_problem:` -  `fact_supports_pitch:` -  "
        "`frame_fits_seat:` -  `right_person:` -",
    )
    with pytest.raises(ValueError, match="cannot read send_it"):
        parse_filled_sheet(text)


def test_parse_filled_sheet_marks_prefilled_rows():
    from gtm_core.eval_calibration import parse_filled_sheet

    text = _sheet(
        "a" * 16,
        "`send_it:` Y  `fact_creates_problem:` -  `fact_supports_pitch:` -  "
        "`frame_fits_seat:` -  `right_person:` -",
    )
    labels = parse_filled_sheet(text, prefilled_ids=["a" * 16])
    assert labels[0].prefilled is True
    labels2 = parse_filled_sheet(text, prefilled_ids=[])
    assert labels2[0].prefilled is False


# --------------------------------------------------------------------------- intra-rater agreement


def test_intra_rater_agreement_perfect_when_repeats_match():
    from gtm_core.eval_calibration import intra_rater_agreement

    labels = [_label("dup", True), _label("dup", True), _label("solo", False)]
    r = intra_rater_agreement(labels)
    assert r["duplicate_rows"] == 1
    assert r["agreement"] == 1.0


def test_intra_rater_agreement_catches_self_disagreement():
    from gtm_core.eval_calibration import intra_rater_agreement

    labels = [_label("dup", True), _label("dup", False)]
    r = intra_rater_agreement(labels)
    assert r["agreement"] == 0.0
    assert r["disagreed_rows"] == ["dup"]


def test_intra_rater_agreement_none_without_duplicates():
    from gtm_core.eval_calibration import intra_rater_agreement

    assert intra_rater_agreement([_label("a", True)])["agreement"] is None


# --------------------------------------------------------------------------- cold-only holdout


def test_seal_holdout_excludes_prefilled_rows_by_default():
    cold = [_label(f"cpos{i}", True) for i in range(6)] + [
        _label(f"cneg{i}", False) for i in range(6)
    ]
    warm = [_label(f"ppos{i}", True, prefilled=True) for i in range(20)]
    train, holdout = seal_holdout(cold + warm, n_per_class=3)
    assert all(not label_.prefilled for label_ in holdout)  # holdout is cold-labeled only
    assert any(label_.prefilled for label_ in train)  # ...but prefilled rows still train


def test_seal_holdout_error_names_the_prefilled_rows_it_excluded():
    cold = [_label("cpos", True), _label("cneg", False)]
    warm = [_label(f"p{i}", True, prefilled=True) for i in range(30)]
    with pytest.raises(ValueError, match="pre-filled"):
        seal_holdout(cold + warm, n_per_class=5)


def test_fully_prefilled_sheet_says_the_holdout_is_gone():
    """--blank 0 removes the sealed holdout. The sheet must say so rather than print
    'the other 0 are deliberately blank ... do the blank ones', which is incoherent."""
    from gtm_core.build_eval_sheet import GoldenRow
    from gtm_core.eval_calibration import render_labeling_sheet

    rows = [
        GoldenRow(
            row_id=f"{i:016x}",
            spec="s.md",
            csv="c.csv",
            touch=1,
            email=f"chris{i}@cascade.example",
            subject="identity in production",
            body="Hi Chris,\n\nSomething happened.\n\nHenry",
            context={"title": "CISO", "company": f"Cascade{i}"},
            injected=False,
            injected_rule=None,
        )
        for i in range(3)
    ]
    prefill = {
        r.row_id: {
            "send_it": True,
            "fact_creates_problem": True,
            "fact_supports_pitch": False,
            "frame_fits_seat": True,
            "right_person": True,
            "note": "fine",
        }
        for r in rows
    }
    sheet = render_labeling_sheet(rows, prefill=prefill)
    assert "deliberately blank" not in sheet
    assert "do the blank ones" not in sheet
    assert "All 3 rows are pre-filled" in sheet
    assert "every row you **change**" in sheet
    assert "`send_it:` ___" not in sheet


# ── holdout purity: the circularity guards ────────────────────────────────────


def _mk_label(row_id: str, send_it: bool, **kw):
    """A minimal valid label. Fictional fixtures only (§R9)."""
    from gtm_core.eval_calibration import Label

    return Label(row_id=row_id, spec_sha256="a" * 16, csv_sha256="b" * 16, send_it=send_it, **kw)


def test_holdout_contains_no_prefilled_row():
    """A pre-filled row cannot validate the judge that pre-filled it.

    Keeping a suggested answer is indistinguishable from agreeing with it, so scoring a
    judge against those rows measures the labeler's willingness to click Next.
    """
    from gtm_core.eval_calibration import seal_holdout

    labels = (
        [_mk_label(f"p{i}", True) for i in range(4)]
        + [_mk_label(f"n{i}", False) for i in range(4)]
        + [_mk_label(f"x{i}", True, prefilled=True) for i in range(4)]
    )
    _train, holdout = seal_holdout(labels, n_per_class=2)
    assert holdout, "empty holdout — every assertion below would pass vacuously"
    assert not any(label_.prefilled for label_ in holdout)


def test_score_refuses_an_empty_holdout(tmp_path):
    """The vacuous-pass guard. An empty holdout satisfies every purity check trivially."""
    from gtm_core.eval_calibration import _load_holdout

    path = tmp_path / "holdout.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="EMPTY"):
        _load_holdout(path)


def test_score_refuses_a_holdout_whose_rows_were_prefilled_by_the_same_judge(tmp_path):
    """Circular validation must be refused loudly, never reported as a pass."""
    import json as _json

    from gtm_core.eval_calibration import _load_holdout

    path = tmp_path / "holdout.json"
    path.write_text(
        _json.dumps([_mk_label("r1", True, prefilled=True).to_dict()]), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="PRE-FILLED"):
        _load_holdout(path)

    # Positive control: a clean holdout loads fine, so the guard is discriminating.
    path.write_text(_json.dumps([_mk_label("r1", True).to_dict()]), encoding="utf-8")
    assert len(_load_holdout(path)) == 1


def test_a_repaired_row_is_excluded_from_judge_predictions(tmp_path):
    """Scoring the judge on copy the judge shaped is `prefilled` one step downstream."""
    from gtm_core.adjudication import Adjudication, write_records
    from gtm_core.eval_calibration import _predictions_from_records

    path = tmp_path / "rec.jsonl"
    write_records(
        [
            Adjudication(
                email="a@x.example", verdict="send", score=4, row_id="r1", repair_attempt=0
            ),
            Adjudication(
                email="b@x.example",
                verdict="send",
                score=4,
                row_id="r2",
                repair_attempt=2,
                repaired=True,
            ),
        ],
        path,
    )
    preds, skipped = _predictions_from_records(path)
    assert set(preds) == {"r1"}, "a repaired row reached the judge-validation predictions"
    assert skipped == 1, "the exclusion was silent — it must be counted and reported"


def test_an_unscored_row_never_becomes_a_prediction(tmp_path):
    """An unscored row must not be silently read as 'predicted sendable'."""
    from gtm_core.adjudication import Adjudication, write_records
    from gtm_core.eval_calibration import _predictions_from_records

    path = tmp_path / "rec.jsonl"
    write_records(
        [Adjudication(email="a@x.example", verdict="", score=0, row_id="r1", unscored=True)],
        path,
    )
    preds, skipped = _predictions_from_records(path)
    assert preds == {}
    assert skipped == 1


# ── R8: the flip-rate silent control ──────────────────────────────────────────


def test_flip_rate_uses_a_genuinely_reversed_rubric_order():
    """The reversal must actually reverse something.

    A flip-rate run against a rubric that was never reordered reports 0% flips and reads
    as the most reassuring number on the page while measuring nothing at all. So the two
    orderings are asserted to DIFFER before any verdict comparison is trusted.
    """
    from agent.mcp.judge.scoring import RUBRIC_ITEMS, rubric_text

    assert len(RUBRIC_ITEMS) > 1, "a one-item rubric cannot be reversed; flip rate is vacuous"
    forward, reverse = rubric_text(), rubric_text(reverse=True)
    assert forward != reverse, (
        "reverse_rubric=True produced an identical rubric — every flip-rate run against it "
        "would report 0% and prove nothing"
    )
    # And the reversal is a true reordering, not a different rubric.
    assert sorted(forward.split("\n")[0:0] or []) == []
    assert {line.split("—")[0].split("`")[1] for line in forward.splitlines()} == {
        line.split("—")[0].split("`")[1] for line in reverse.splitlines()
    }, "the reversed rubric contains different ITEMS, not the same items in another order"


def test_flip_rate_of_a_stable_judge_is_zero_and_of_a_coin_is_one():
    """Sanity on the metric itself, in both directions."""
    from gtm_core.eval_calibration import flip_rate

    assert flip_rate([(True, True), (False, False)]) == 0.0
    assert flip_rate([(True, False), (False, True)]) == 1.0


def test_an_unmeasured_flip_rate_is_not_reported_as_a_pass(tmp_path, capsys):
    """A judge that clears three bars with the fourth unmeasured is provisional, not valid."""
    import json as _json

    from gtm_core.adjudication import Adjudication, write_records
    from gtm_core.eval_calibration import main

    holdout = tmp_path / "h.json"
    labels = [_mk_label(f"p{i}", True) for i in range(3)] + [
        _mk_label(f"n{i}", False) for i in range(3)
    ]
    holdout.write_text(_json.dumps([label_.to_dict() for label_ in labels]), encoding="utf-8")

    preds = tmp_path / "rec.jsonl"
    write_records(
        [
            Adjudication(
                email=f"{label_.row_id}@x.example",
                verdict="send" if label_.send_it else "drop",
                score=4,
                row_id=label_.row_id,
                repair_attempt=0,
            )
            for label_ in labels
        ],
        preds,
    )
    rc = main(
        ["score", "--profile", "example", "--holdout", str(holdout), "--predictions", str(preds)]
    )
    out = capsys.readouterr().out
    assert "NOT MEASURED" in out, "an unmeasured flip rate was printed as a number"
    assert rc == 1, (
        "a perfect-agreement judge with an UNMEASURED stability bar exited 0 — that reads "
        "as validated when one of the four bars was never tested"
    )


def test_holdout_contains_no_repaired_row():
    """Repaired copy is judge-shaped. Validating the judge on it is circular.

    `prefilled` guards the same failure one step upstream; without this, the repair loop
    quietly reintroduces it — the operator labels copy the judge wrote, and those labels
    become the judge's own report card.
    """
    from gtm_core.eval_calibration import seal_holdout

    labels = (
        [_mk_label(f"p{i}", True) for i in range(4)]
        + [_mk_label(f"n{i}", False) for i in range(4)]
        + [_mk_label(f"r{i}", False, repaired=True) for i in range(4)]
    )
    _train, holdout = seal_holdout(labels, n_per_class=2)
    assert holdout, "empty holdout — the assertion below would pass vacuously"
    assert not any(label_.repaired for label_ in holdout), (
        "a repaired row reached the validation holdout"
    )


def test_a_repaired_label_still_round_trips_and_stays_training_data():
    """Excluded from the holdout is not the same as discarded — the stratum must survive."""
    from gtm_core.eval_calibration import label_from_dict, seal_holdout

    repaired = _mk_label("r1", False, repaired=True)
    assert label_from_dict(repaired.to_dict()).repaired is True, (
        "the repaired flag did not survive a dict round trip"
    )
    labels = (
        [_mk_label(f"p{i}", True) for i in range(3)]
        + [_mk_label(f"n{i}", False) for i in range(3)]
        + [repaired]
    )
    train, _holdout = seal_holdout(labels, n_per_class=2)
    assert any(label_.repaired for label_ in train), (
        "the repaired row was dropped entirely instead of moved to the training half"
    )


def test_verify_holdout_refuses_a_repaired_row(tmp_path):
    """The skill body promises this refusal, so it has to be real, not aspirational."""
    import json as _json

    from gtm_core.eval_calibration import _load_holdout

    path = tmp_path / "h.json"
    path.write_text(_json.dumps([_mk_label("r1", True, repaired=True).to_dict()]), encoding="utf-8")
    with pytest.raises(ValueError, match="REPAIRED"):
        _load_holdout(path)


def test_verify_holdout_refuses_a_single_class_holdout(tmp_path, capsys):
    """One empty class makes TPR or TNR undefined — a bar that cannot be measured.

    Reporting three bars and an undefined fourth as a pass is the same shape as the
    unmeasured flip rate: a number-shaped absence.
    """
    import json as _json

    from gtm_core.eval_calibration import main

    path = tmp_path / "h.json"
    path.write_text(
        _json.dumps([_mk_label(f"p{i}", True).to_dict() for i in range(4)]), encoding="utf-8"
    )
    rc = main(["verify-holdout", "--profile", "example", "--holdout", str(path)])
    assert rc == 1
    assert "one class is empty" in capsys.readouterr().err


def test_verify_holdout_passes_a_clean_balanced_holdout(tmp_path, capsys):
    """Positive control: a check that refuses everything proves nothing."""
    import json as _json

    from gtm_core.eval_calibration import main

    path = tmp_path / "h.json"
    labels = [_mk_label(f"p{i}", True) for i in range(3)] + [
        _mk_label(f"n{i}", False) for i in range(3)
    ]
    path.write_text(_json.dumps([label_.to_dict() for label_ in labels]), encoding="utf-8")
    assert main(["verify-holdout", "--profile", "example", "--holdout", str(path)]) == 0
    assert "holdout clean: 6 row(s)" in capsys.readouterr().out


# --------------------------------------------------------- row_id is the join key


def test_row_id_is_independent_of_how_the_caller_spelled_the_path(tmp_path, monkeypatch):
    """Absolute and content-root-relative spellings must produce ONE id.

    `row_id` joins the labeling sheet to the judge's adjudication records. Hashing the
    caller's raw string made the id depend on spelling: the sheet builder resolves an
    absolute path, a judge invocation passes `content/<profile>/...`. On 2026-08-23 a full
    judge pass joined to 0 of 30 sheet rows and nothing errored, because a mismatched key
    looks exactly like a row nobody judged.
    """
    from gtm_core.eval_calibration import _row_id

    # A relative path resolves against the CWD, exactly as it does for every other caller
    # in this repo — so the realistic pair is "content/demo/spec.md" typed at the repo root
    # versus the absolute path the sheet builder stores.
    root = tmp_path / "content"
    (root / "demo").mkdir(parents=True)
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(root))
    monkeypatch.chdir(tmp_path)

    rel_spec, rel_csv = "content/demo/spec.md", "content/demo/rows.csv"
    abs_spec, abs_csv = str(root / "demo/spec.md"), str(root / "demo/rows.csv")

    assert _row_id(abs_spec, abs_csv, "sam@acme.example", 1) == _row_id(
        rel_spec, rel_csv, "sam@acme.example", 1
    )


def test_row_id_still_separates_two_different_specs(tmp_path, monkeypatch):
    """The normalisation must not collapse distinct files into one id.

    The cheap way to make the ids agree would be to hash only the basename — every drafted
    cell uses `spec.md` + `rows.csv`, so that would give all nine cells the same key.
    """
    from gtm_core.eval_calibration import _row_id

    root = tmp_path / "content"
    (root / "demo" / "cell-a").mkdir(parents=True)
    (root / "demo" / "cell-b").mkdir(parents=True)
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(root))
    monkeypatch.chdir(tmp_path)

    a = _row_id("content/demo/cell-a/spec.md", "content/demo/cell-a/rows.csv", "s@a.example", 1)
    b = _row_id("content/demo/cell-b/spec.md", "content/demo/cell-b/rows.csv", "s@a.example", 1)
    assert a != b


def test_no_sealed_holdout_means_not_calibrated(tmp_path):
    """The question nothing could ask until 2026-08-27: has this judge ever been measured?

    It had not — for any profile — while every verdict it produced looked exactly as
    authoritative as a validated one.
    """
    from gtm_core.eval_calibration import evals_dir, is_calibrated, sealed_holdouts

    assert is_calibrated("acme", tmp_path) is False
    assert sealed_holdouts("acme", tmp_path) == []

    d = evals_dir("acme", tmp_path)
    d.mkdir(parents=True)
    (d / "labels-2026-01-01.jsonl").write_text("{}\n", encoding="utf-8")
    assert is_calibrated("acme", tmp_path) is False, "labels are not a sealed holdout"

    (d / "run-2026-01-01-holdout.json").write_text("[]", encoding="utf-8")
    assert is_calibrated("acme", tmp_path) is True


def test_sealed_holdouts_are_ordered_by_filename_not_mtime(tmp_path):
    """A fresh clone or `cp -r` rewrites every mtime at once; the dates are in the names."""
    d = ec.evals_dir("acme", content_root=tmp_path)
    d.mkdir(parents=True)
    older = d / "2026-08-01-holdout.json"
    newer = d / "2026-08-20-holdout.json"
    for p in (older, newer):
        p.write_text("{}", encoding="utf-8")
    # Invert the mtimes: the older-named file looks freshest on disk.
    os.utime(newer, (1, 1))
    os.utime(older, (10**9, 10**9))

    found = ec.sealed_holdouts("acme", content_root=tmp_path)
    assert [p.name for p in found] == [newer.name, older.name]
