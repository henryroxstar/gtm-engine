"""Tests for the reading pass — sampling, coverage, ranking, and rule candidacy.

All fixtures invented (docs/RULES.md R9). The behaviours pinned here are the ones that
make a read auditable rather than anecdotal: the same list must yield the same sample,
the rare stratum must not be starved by the common one, and "I read 50" must not be
reportable as "the list is reviewed".
"""

from __future__ import annotations

import json

from gtm_core.adjudication import (
    Adjudication,
    collapsed_axes,
    coverage,
    covered_classes,
    novel_classes,
    rank,
    read_records,
    sample,
    stratify,
    stratum_of,
)


def _rows(n=30):
    out = []
    for i in range(n):
        out.append(
            {
                "email": f"person{i}@acct{i}.example",
                "company": f"Acct {i}",
                "seat": ["security", "exec", "technical"][i % 3],
                "tier": "A" if i % 5 else "B",
                "signal_clause": "" if i % 4 == 0 else "opened an AI governance program",
                # The messaging axis: which hook-matrix argument this row would receive.
                "cell": ["CISO x Partner agents", "CEO x Sales-cycle stall"][i % 2],
            }
        )
    return out


def test_stratum_derives_signal_rather_than_reading_a_column():
    row = {"seat": "exec", "tier": "A", "cell": "CEO x Stall"}
    assert stratum_of({**row, "signal_clause": "x"}) == "exec|signal|A|CEO x Stall"
    assert stratum_of({**row, "signal_clause": ""}) == "exec|generic|A|CEO x Stall"


def test_missing_axis_becomes_a_visible_placeholder_not_a_silent_drop():
    assert stratum_of({"signal_clause": "x"}) == "-|signal|-|-"


def test_cell_is_a_stratum_so_a_draw_cannot_be_blind_to_the_argument():
    """Two rows identical on seat/signal/tier but carrying DIFFERENT arguments must land in
    different strata.

    Without this the 2026-08-21 sheet called itself stratified while every one of its 30 rows
    carried one of two hook-matrix cells out of 74 — copy is scoped per list, so seat varies
    who receives the email and not which argument they receive. An eval blind to the messaging
    axis reports coverage it does not have."""
    base = {"seat": "security", "tier": "A", "signal_clause": "x"}
    assert stratum_of({**base, "cell": "CISO x Partner agents"}) != stratum_of(
        {**base, "cell": "CISO x Compliance event"}
    )


def test_stratify_partitions_every_row_exactly_once():
    rows = _rows()
    buckets = stratify(rows)
    assert sum(len(v) for v in buckets.values()) == len(rows)


def test_collapsed_axes_names_what_does_not_vary():
    rows = [{"seat": "security", "tier": "A", "signal_clause": "x"} for _ in range(5)]
    assert set(collapsed_axes(rows)) == {"seat", "tier", "signal", "cell"}


def test_collapsed_axes_reports_a_single_argument_list():
    """The guard that would have caught the 2026-08-21 draw. Rows varying on every other axis
    but sharing ONE hook cell must report `cell` as collapsed — otherwise a two-argument
    sample is indistinguishable from a broad one in the coverage line."""
    rows = [
        {"seat": s, "tier": "A", "signal_clause": "x", "cell": "CISO x Partner agents"}
        for s in ("security", "cto", "ai-platform")
    ]
    assert "cell" in collapsed_axes(rows)
    assert "seat" not in collapsed_axes(rows)


def test_sample_is_deterministic_across_runs():
    """A sample nobody can reproduce is a sample nobody can check — which is why this
    hashes the row's own identity rather than seeding a PRNG."""
    rows = _rows()
    assert [r["email"] for r in sample(rows, 9)] == [r["email"] for r in sample(rows, 9)]


def test_seed_varies_the_exemplars_without_losing_reproducibility():
    """Two properties that pull against each other, both required.

    A fixed ordering key means consecutive evals re-read the same rows forever and the rest of
    the list is never exercised. True randomness means a sheet cannot be rebuilt and two
    labelers cannot be compared. Seeding the hash gives variety ACROSS evals and determinism
    WITHIN one."""
    rows = _rows()
    a1 = [r["email"] for r in sample(rows, 9, seed="2026-08-23")]
    a2 = [r["email"] for r in sample(rows, 9, seed="2026-08-23")]
    b = [r["email"] for r in sample(rows, 9, seed="2026-09-01")]
    assert a1 == a2, "same seed must reproduce exactly"
    assert a1 != b, "a different seed must draw different exemplars"


def test_sample_spreads_across_arguments_before_repeating_one():
    """Coverage-first on the messaging axis: with two cells available, a draw of two must
    contain both rather than two rows of the same argument."""
    rows = [
        {
            "email": f"p{i}@acct{i}.example",
            "seat": "security",
            "tier": "A",
            "signal_clause": "x",
            "cell": "CISO x Partner agents" if i < 5 else "CISO x Compliance event",
        }
        for i in range(10)
    ]
    assert len({r["cell"] for r in sample(rows, 2)}) == 2


def test_sample_is_stable_under_input_reordering():
    rows = _rows()
    assert sorted(r["email"] for r in sample(rows, 9)) == sorted(
        r["email"] for r in sample(list(reversed(rows)), 9)
    )


def test_sample_covers_every_stratum_before_deepening_any():
    """Proportional allocation spends a scarce reading budget re-reading the template
    that is already best exercised, and starves the rare stratum where the copy is most
    likely wrong."""
    rows = _rows()
    strata = set(stratify(rows))
    picked = sample(rows, len(strata))
    assert {stratum_of(r) for r in picked} == strata


def test_sample_caps_at_the_list_size():
    rows = _rows(4)
    assert len(sample(rows, 100)) == 4


def test_sample_of_zero_or_empty_is_empty():
    assert sample(_rows(), 0) == []
    assert sample([], 10) == []


def test_coverage_reports_unread_strata():
    rows = _rows()
    read = [Adjudication(email=rows[0]["email"], verdict="send", score=4)]
    c = coverage(rows, read)
    assert c.adjudicated == 1
    assert c.read == 1
    assert c.strata > 1
    assert c.unread_strata
    assert stratum_of(rows[0]) not in c.unread_strata


def test_full_coverage_leaves_nothing_unread():
    rows = _rows()
    read = [Adjudication(email=r["email"], verdict="send", score=3) for r in rows]
    c = coverage(rows, read)
    assert c.unread_strata == []
    assert c.share == 1.0


def test_rank_puts_verdict_ahead_of_score():
    """ "Should this be sent at all" dominates "would they reply" — a high-scoring drop
    never outranks a send."""
    records = [
        Adjudication(email="c@x.example", verdict="drop", score=5),
        Adjudication(email="a@x.example", verdict="send", score=2),
        Adjudication(email="b@x.example", verdict="re-angle", score=5),
    ]
    assert [a.email for a in rank(records)] == ["a@x.example", "b@x.example", "c@x.example"]


def test_rank_orders_by_score_within_a_verdict():
    records = [
        Adjudication(email="lo@x.example", verdict="send", score=2),
        Adjudication(email="hi@x.example", verdict="send", score=5),
    ]
    assert [a.email for a in rank(records)] == ["hi@x.example", "lo@x.example"]


def test_novel_classes_are_the_only_rule_candidates():
    records = [
        Adjudication(email="a@x.example", verdict="drop", score=1, defect_class="proof-unmapped"),
        Adjudication(email="b@x.example", verdict="drop", score=1, defect_class="proof-unmapped"),
        Adjudication(email="c@x.example", verdict="send", score=3, defect_class="cta-overclaim"),
    ]
    known = ["cta-overclaim", "hedge-missing"]
    assert novel_classes(records, known) == {"proof-unmapped": 2}


def test_a_covered_class_recurring_is_reported_as_an_inert_gate():
    """The 2026-08-19 failure: a --signoff default silently disabled four CTA rules, so
    a class with a rule kept appearing in reads. That is worse than a missing rule."""
    records = [
        Adjudication(email="a@x.example", verdict="drop", score=1, defect_class="cta-overclaim")
    ]
    assert covered_classes(records, ["cta-overclaim"]) == {"cta-overclaim": 1}
    assert novel_classes(records, ["cta-overclaim"]) == {}


def test_read_records_round_trips_jsonl(tmp_path):
    path = tmp_path / "adj.jsonl"
    path.write_text(
        "# a comment line is skipped\n"
        + json.dumps({"email": "a@x.example", "verdict": "SEND", "score": "4"})
        + "\n\n",
        encoding="utf-8",
    )
    records = read_records(path)
    assert len(records) == 1
    assert records[0].verdict == "send"  # normalised
    assert records[0].score == 4


# ── the repair loop: the CLI is the stopping authority, not the model ──────────


def test_a_row_at_the_cap_is_refused_a_fourth_attempt():
    """`repair-queue` must hard-refuse, not merely stop suggesting.

    The cap lives in deterministic code on purpose. A model asked to police its own
    retry budget is a model that can be argued out of it.
    """
    from gtm_core.adjudication import REPAIR_ATTEMPT_CAP, repair_queue

    at_cap = Adjudication(
        email="a@x.example", verdict="re-angle", score=2, repair_attempt=REPAIR_ATTEMPT_CAP
    )
    [candidate] = repair_queue([at_cap])
    assert not candidate.eligible
    assert "cap" in candidate.reason


def test_a_row_under_the_cap_is_eligible():
    """The positive control. A queue that refuses everything is as useless as one that
    refuses nothing, and both print as 'no rows repaired'."""
    from gtm_core.adjudication import repair_queue

    [candidate] = repair_queue(
        [Adjudication(email="a@x.example", verdict="re-angle", score=2, repair_attempt=1)]
    )
    assert candidate.eligible, "a row well under the cap was refused — the loop can never run"


def test_a_record_with_no_recorded_attempt_count_is_refused_not_assumed_zero():
    """Defaulting an unknown attempt count to 0 restarts a row already at the cap.

    This is the silent version of an unbounded loop: nothing errors, the row simply gets
    repaired forever, three attempts at a time.
    """
    from gtm_core.adjudication import repair_queue

    legacy = Adjudication(email="a@x.example", verdict="re-angle", score=2)  # repair_attempt=None
    [candidate] = repair_queue([legacy])
    assert not candidate.eligible
    assert "not recorded" in candidate.reason


def test_an_old_record_round_trips_without_inventing_an_attempt_count(tmp_path):
    """A pre-repair-loop JSONL line must load as UNKNOWN, never as zero."""
    path = tmp_path / "old.jsonl"
    path.write_text(
        json.dumps({"email": "a@x.example", "verdict": "re-angle", "score": 2}) + "\n",
        encoding="utf-8",
    )
    [record] = read_records(path)
    assert record.repair_attempt is None, "an old record was silently assigned repair_attempt=0"

    # Positive control: an explicit 0 must load as 0, not as None.
    path.write_text(
        json.dumps({"email": "a@x.example", "verdict": "re-angle", "score": 2, "repair_attempt": 0})
        + "\n",
        encoding="utf-8",
    )
    assert read_records(path)[0].repair_attempt == 0


def test_a_send_verdict_is_never_a_repair_candidate():
    from gtm_core.adjudication import repair_queue

    assert (
        repair_queue([Adjudication(email="a@x.example", verdict="send", score=5, repair_attempt=0)])
        == []
    )


def test_an_unscored_row_is_not_repaired_toward_nothing():
    """No verdict means no target. Re-composing a row the judge could not read is guessing."""
    from gtm_core.adjudication import repair_queue

    [candidate] = repair_queue(
        [Adjudication(email="a@x.example", verdict="", score=0, unscored=True, repair_attempt=0)]
    )
    assert not candidate.eligible
    assert "unscored" in candidate.reason


def test_repaired_rows_are_reported_as_their_own_stratum(tmp_path):
    """Pooling repaired and cold copy makes the repair loop's effect unmeasurable.

    The loop is proxy-metric optimisation by construction; the only thing that makes it
    safe to keep is being able to answer 'is judge-shaped copy better or worse' with a
    number.
    """
    from gtm_core.adjudication import write_records

    path = tmp_path / "rec.jsonl"
    write_records(
        [
            Adjudication(email="a@x.example", verdict="send", score=4, repair_attempt=0),
            Adjudication(
                email="b@x.example", verdict="send", score=4, repair_attempt=2, repaired=True
            ),
        ],
        path,
    )
    loaded = read_records(path)
    assert [r.repaired for r in loaded] == [False, True], (
        "the repaired flag did not survive a round trip — repaired and cold copy are now "
        "indistinguishable on disk"
    )


def test_a_row_the_judge_could_not_parse_is_recorded_as_unscored_not_dropped(tmp_path):
    """A vanished row makes a partial batch look complete."""
    from gtm_core.adjudication import completeness, write_records

    rows = [{"email": "a@x.example"}, {"email": "b@x.example"}]
    path = tmp_path / "rec.jsonl"
    write_records(
        [
            Adjudication(email="a@x.example", verdict="send", score=4, repair_attempt=0),
            Adjudication(email="b@x.example", verdict="", score=0, unscored=True, repair_attempt=0),
        ],
        path,
    )
    result = completeness(rows, read_records(path))
    assert result.complete, "an unscored row was treated as a missing row"
    assert (result.scored, result.unscored) == (1, 1), (
        "the unscored row is not being counted separately — coverage now overstates itself"
    )


# --- disposal audit: what happened to the rows that did NOT send -------------------------
#
# Regression cover for 2026-08-27, when a run scored 963 researched accounts, rejected all
# but two, filed none of the rejections anywhere, and reported "zero survived" as a result.


def test_stranded_re_angle_rows_are_reported_not_silently_dropped():
    """A re-angle filed nowhere is a work queue the caller lost, not a negative result."""
    from gtm_core.adjudication import disposal_audit

    recs = [
        Adjudication(email="a@x.example", verdict="send", score=4, repair_attempt=0),
        Adjudication(email="b@x.example", verdict="re-angle", score=2, repair_attempt=0),
        Adjudication(email="c@x.example", verdict="re-angle", score=2, repair_attempt=0),
    ]
    d = disposal_audit(recs, accounted_for=[])
    assert d.stranded == 2, "re-angle rows filed nowhere are not being counted"
    assert d.failed, "a batch that stranded its re-angle rows reported clean"

    # Naming a destination for them clears the finding — the point is accounting, not volume.
    ok = disposal_audit(recs, accounted_for=["b@x.example", "c@x.example"])
    assert ok.stranded == 0 and not ok.failed


def test_a_drop_carrying_a_targeting_defect_is_flagged_as_a_misfiled_re_angle():
    """`fact_creates_problem` says the ARGUMENT was mis-aimed, never that the account is dead.

    The judge does not reliably honour its own verdict vocabulary: every rejection on
    2026-08-27 came back `drop` while its own note described a re-angle. A caller reading the
    verdict word alone retires a healthy account, so the defect class has to be able to
    contradict the word.
    """
    from gtm_core.adjudication import disposal_audit

    recs = [
        Adjudication(
            email="a@x.example",
            verdict="drop",
            score=1,
            defect_class="fact_creates_problem",
            repair_attempt=0,
        ),
        # A genuine copy defect stays a real drop — the check must not launder every drop.
        Adjudication(
            email="b@x.example", verdict="drop", score=1, defect_class="em_dash", repair_attempt=0
        ),
    ]
    d = disposal_audit(recs, accounted_for=[])
    assert d.misfiled_drop == 1, "a targeting defect recorded as `drop` went unflagged"
    assert "re-angle, not a do-not-contact" in " ".join(d.findings)

    # Filing it clears the FINDING but not the COUNT: the miscategorisation stays visible
    # (it is worth seeing that the judge said `drop` for a targeting defect at all), while
    # the failure is about accounting, exactly as it is for a stranded re-angle.
    filed = disposal_audit(recs, accounted_for=["a@x.example"])
    assert filed.misfiled_drop == 1, "filing a row must not erase the miscategorisation"
    assert filed.misfiled_unfiled == 0
    assert not filed.failed, "a filed misfiled-drop should not keep failing the gate"


def test_uncalibrated_verdicts_are_flagged_and_unknown_is_not_treated_as_calibrated():
    """`calibrated=None` (record predates the field) must not read as a passing measurement."""
    from gtm_core.adjudication import disposal_audit

    unknown = Adjudication(email="a@x.example", verdict="drop", score=1, repair_attempt=0)
    assert unknown.calibrated is None, "the default must be 'unrecorded', never False"

    measured_bad = Adjudication(email="b@x.example", verdict="drop", score=1, repair_attempt=0)
    measured_bad.calibrated = False
    d = disposal_audit([unknown, measured_bad], accounted_for=[])
    assert d.uncalibrated == 1, "only the demonstrably-unvalidated row should count"
    assert "UNCALIBRATED" in " ".join(d.findings)


def test_all_exports_the_disposal_symbols():
    """`disposal_audit` shipped unexported — `from ... import *` could not reach it."""
    import gtm_core.adjudication as adj

    for name in ("Disposal", "disposal_audit", "TARGETING_DEFECTS"):
        assert name in adj.__all__, f"{name} is public API but missing from __all__"
        assert hasattr(adj, name)
