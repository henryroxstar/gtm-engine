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
    # Reported under the canonical (snake_case) spelling since 2026-09-03: rule ids are
    # kebab, judge classes are free text, and both go through one normaliser.
    assert novel_classes(records, known) == {"proof_unmapped": 2}


def test_a_covered_class_recurring_is_reported_as_an_inert_gate():
    """The 2026-08-19 failure: a --signoff default silently disabled four CTA rules, so
    a class with a rule kept appearing in reads. That is worse than a missing rule."""
    records = [
        Adjudication(email="a@x.example", verdict="drop", score=1, defect_class="cta-overclaim")
    ]
    assert covered_classes(records, ["cta-overclaim"]) == {"cta_overclaim": 1}
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


def test_the_merged_fact_field_is_a_targeting_defect():
    """`fact_creates_problem` merged into `fact_earns_its_place` on the label side on
    2026-09-02 (gtm_core.eval_calibration.LABEL_FIELDS). The judge's `defect_class`
    vocabulary is independent of the label schema, but the survivor name must still be
    honoured here for records the judge writes going forward.
    """
    from gtm_core.adjudication import disposal_audit

    recs = [
        Adjudication(
            email="a@x.example",
            verdict="drop",
            score=1,
            defect_class="fact_earns_its_place",
            repair_attempt=0,
        ),
    ]
    d = disposal_audit(recs, accounted_for=[])
    assert d.misfiled_drop == 1, "the post-merge defect name was not recognised as targeting"


def test_the_pre_merge_defect_string_is_still_targeting_after_the_merge():
    """Regression guard: dropping `fact_creates_problem` from TARGETING_DEFECTS once its
    label-side field merged into `fact_earns_its_place` would silently reclassify every
    `defect_class="fact_creates_problem"` record the judge already wrote (2026-08-27
    onward, before the merge) from misfiled-drop to ordinary drop — a behaviour change
    disguised as a rename. It must keep firing.
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
    ]
    d = disposal_audit(recs, accounted_for=[])
    assert d.misfiled_drop == 1, "the pre-merge defect string stopped being recognised"


def test_fact_supports_pitch_was_never_targeting_and_still_is_not():
    """`fact_supports_pitch` and `bridge_depends_on_fact` were never in TARGETING_DEFECTS —
    only `fact_creates_problem` was. The merge must not widen what counts as 'mis-aimed'
    under cover of the rename; a drop carrying either of the other two stays a real drop.
    """
    from gtm_core.adjudication import disposal_audit

    recs = [
        Adjudication(
            email="a@x.example",
            verdict="drop",
            score=1,
            defect_class="fact_supports_pitch",
            repair_attempt=0,
        ),
        Adjudication(
            email="b@x.example",
            verdict="drop",
            score=1,
            defect_class="bridge_depends_on_fact",
            repair_attempt=0,
        ),
    ]
    d = disposal_audit(recs, accounted_for=[])
    assert d.misfiled_drop == 0, "fact_supports_pitch/bridge_depends_on_fact must not be targeting"


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


def test_all_exports_the_tally_symbols():
    """`tally` shipped unexported once already (2026-08-31) — pin the whole surface."""
    import gtm_core.adjudication as adj

    for name in (
        "TallyRow",
        "Tally",
        "unstable_row_id",
        "unstable_bodies",
        "tally",
        "collapse_touches",
        "send_set",
        "write_tally",
        "completeness",
        "covered_classes",
    ):
        assert name in adj.__all__, f"{name} is public API but missing from __all__"
        assert hasattr(adj, name)


def test_the_same_recipient_re_judged_is_one_group_not_two():
    """The 2026-08-30 pool-599 bug, minimised to two records.

    `row_id` is `sha256(spec|csv|email|touch)[:16]`, so two independent runs over the
    same recipient mint two different ids and never collide. Grouping by it made all
    1298 real records their own singleton group — each trivially unanimous with
    itself — and the tool printed "flip rate 0.0%, ties 0" over a pool where 215 of 599
    recipients (36%) had actually changed verdict on an identical body. One recipient
    judged twice by two runs is ONE unit with TWO runs, not two units.
    """
    from gtm_core.adjudication import tally

    t = tally(
        {
            "run-a": [
                Adjudication(
                    email="a@x.example",
                    verdict="send",
                    score=4,
                    row_id="aaaa000000000000",
                    body_hash="bh1",
                )
            ],
            "run-b": [
                Adjudication(
                    email="a@x.example",
                    verdict="drop",
                    score=1,
                    row_id="bbbb111111111111",
                    body_hash="bh1",
                )
            ],
        }
    )
    assert t.units == 1, "two runs over one recipient produced two groups — row_id keying is back"
    assert t.rows[0].runs == 2
    assert t.split == 1 and t.split_rate == 1.0, "a changed verdict was counted as stable"


def test_a_single_run_of_unique_row_ids_does_not_trip_the_unstable_key_guard():
    """The positive control on the guard: on one legitimate run every row_id IS unique,
    and that must not be reported as instability."""
    from gtm_core.adjudication import unstable_row_id

    recs = [
        Adjudication(email="a@x.example", verdict="send", score=4, row_id="r1"),
        Adjudication(email="b@x.example", verdict="drop", score=1, row_id="r2"),
        Adjudication(email="c@x.example", verdict="re-angle", score=2, row_id="r3"),
    ]
    assert unstable_row_id(recs) == ""


def test_the_guard_fires_when_one_recipient_carries_two_row_ids():
    from gtm_core.adjudication import unstable_row_id

    recs = [
        Adjudication(email="a@x.example", verdict="send", score=4, row_id="r1"),
        Adjudication(email="a@x.example", verdict="drop", score=1, row_id="r2"),
    ]
    msg = unstable_row_id(recs)
    assert msg and "NOT stable" in msg
    assert "a@x.example" in msg


def test_the_guard_fires_on_a_mixed_row_id_namespace():
    """Some records carry a row_id and some do not (e.g. a hand-written record predating
    the field, mixed with judge output) — a `row_id or email` fallback would key those
    two sets in different namespaces and split a recipient present in both."""
    from gtm_core.adjudication import unstable_row_id

    recs = [
        Adjudication(email="a@x.example", verdict="send", score=4, row_id="r1"),
        Adjudication(email="a@x.example", verdict="send", score=4, row_id=""),
    ]
    msg = unstable_row_id(recs)
    assert msg and "no row_id" in msg


def test_a_row_id_spanning_two_recipients_is_refused_as_corrupt():
    """email is inside sha256(spec|csv|email|touch) — this cannot arise from the real
    hash function, so seeing it means the input was hand-edited."""
    from gtm_core.adjudication import unstable_row_id

    recs = [
        Adjudication(email="a@x.example", verdict="send", score=4, row_id="shared"),
        Adjudication(email="b@x.example", verdict="drop", score=1, row_id="shared"),
    ]
    msg = unstable_row_id(recs)
    assert msg and "span more than one" in msg


def test_unscored_records_do_not_vote():
    """`_VERDICT_SEVERITY.get("", 0) == 0` ties an unscored record with `send` if it is
    ever allowed to vote — it must never reach the Counter at all."""
    from gtm_core.adjudication import tally

    t = tally(
        {
            "run-a": [Adjudication(email="a@x.example", verdict="send", score=4, row_id="r1")],
            "run-b": [Adjudication(email="a@x.example", verdict="send", score=4, row_id="r2")],
            "run-c": [
                Adjudication(email="a@x.example", verdict="", score=0, row_id="r3", unscored=True)
            ],
        }
    )
    row = t.rows[0]
    assert row.runs == 3
    assert row.scored_runs == 2
    assert "" not in row.verdict_counts
    assert row.verdict == "send" and row.policy == "unanimous"


def test_a_unit_with_only_unscored_records_is_no_verdict_not_contested():
    from gtm_core.adjudication import tally

    t = tally(
        {
            "run-a": [
                Adjudication(email="a@x.example", verdict="", score=0, row_id="r1", unscored=True)
            ],
        }
    )
    row = t.rows[0]
    assert row.policy == "no-verdict"
    assert row.contested is False
    assert row.unscored is True
    assert t.no_verdict == 1 and t.contested == 0


def test_a_tie_is_emitted_as_contested_and_never_resolved_toward_drop():
    """44 of 156 real ties in the pool-599 incident erased a `send` vote under
    `max(unique_v, key=_VERDICT_SEVERITY)` — a tie must settle nothing, in either
    direction, rather than silently favour the harsher reading."""
    from gtm_core.adjudication import tally

    t = tally(
        {
            "run-a": [
                Adjudication(
                    email="a@x.example", verdict="send", score=4, row_id="r1", body_hash="bh"
                )
            ],
            "run-b": [
                Adjudication(
                    email="a@x.example", verdict="drop", score=1, row_id="r2", body_hash="bh"
                )
            ],
        }
    )
    row = t.rows[0]
    assert row.verdict == "", "a tie must not settle a verdict"
    assert row.verdict != "drop", "the erased-send-vote bug: a tie resolved toward severity"
    assert row.contested is True
    assert row.unscored is True
    assert t.contested == 1


def test_a_contested_row_cannot_be_written_as_a_settled_verdict():
    """End-to-end: a contested tally row must reach `write-verdicts` as `unscored` and
    never appear as a `judge_verdict` in the enrollment CSV — this is the actual property
    the whole design exists to guarantee, not just an internal field."""
    import csv

    from gtm_core.adjudication import main, tally, write_tally

    t = tally(
        {
            "run-a": [
                Adjudication(
                    email="a@x.example", verdict="send", score=4, row_id="r1", body_hash="bh1"
                )
            ],
            "run-b": [
                Adjudication(
                    email="a@x.example", verdict="drop", score=1, row_id="r2", body_hash="bh1"
                )
            ],
            "run-c": [
                Adjudication(
                    email="b@x.example", verdict="send", score=4, row_id="r3", body_hash="bh2"
                )
            ],
        }
    )

    def _run(tmp_path):
        csv_path = tmp_path / "list.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["email"])
            w.writeheader()
            w.writerow({"email": "a@x.example"})
            w.writerow({"email": "b@x.example"})
        records_path = tmp_path / "tally.jsonl"
        write_tally(t.rows, records_path)
        out = tmp_path / "out.csv"
        assert (
            main(
                [
                    "write-verdicts",
                    "--csv",
                    str(csv_path),
                    "--records",
                    str(records_path),
                    "--out",
                    str(out),
                ]
            )
            == 0
        )
        rows = {r["email"]: r for r in csv.DictReader(out.open(encoding="utf-8"))}
        assert rows["a@x.example"]["judge_verdict"] == "", "a contested row settled a verdict"
        assert rows["b@x.example"]["judge_verdict"] == "send", "an unanimous row went unwritten"

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        _run(Path(td))


def test_a_strict_majority_settles_and_records_which_policy_settled_it():
    from gtm_core.adjudication import tally

    t = tally(
        {
            "run-a": [Adjudication(email="a@x.example", verdict="send", score=4, row_id="r1")],
            "run-b": [Adjudication(email="a@x.example", verdict="send", score=4, row_id="r2")],
            "run-c": [Adjudication(email="a@x.example", verdict="drop", score=1, row_id="r3")],
        }
    )
    row = t.rows[0]
    assert row.verdict == "send" and row.policy == "majority"
    assert row.verdict_counts == {"send": 2, "drop": 1}


def test_the_representative_does_not_depend_on_file_order():
    """The old `matching[0]` picked whichever file was listed first — `tally(a, b)` and
    `tally(b, a)` must be byte-identical."""
    from gtm_core.adjudication import tally

    a = Adjudication(email="a@x.example", verdict="send", score=4, row_id="r1", evidence="from A")
    b = Adjudication(email="a@x.example", verdict="send", score=5, row_id="r2", evidence="from B")

    t1 = tally({"run-a": [a], "run-b": [b]})
    t2 = tally({"run-b": [b], "run-a": [a]})
    assert t1.rows[0].to_dict() == t2.rows[0].to_dict()


def test_score_is_the_ensemble_not_one_runs_guess():
    from gtm_core.adjudication import tally

    t = tally(
        {
            "run-a": [Adjudication(email="a@x.example", verdict="send", score=2, row_id="r1")],
            "run-b": [Adjudication(email="a@x.example", verdict="send", score=4, row_id="r2")],
            "run-c": [Adjudication(email="a@x.example", verdict="send", score=5, row_id="r3")],
        }
    )
    row = t.rows[0]
    assert row.score == 4, "median of the agreeing scores"
    assert row.score_range == (2, 5)


def test_a_unit_whose_runs_judged_different_bodies_is_refused_not_reported_as_a_split():
    """A body_hash mismatch means the runs scored different emails, not that the judge
    was unstable — this must be a hard refusal, never a reported disagreement."""
    from gtm_core.adjudication import unstable_bodies

    same = [
        Adjudication(email="a@x.example", verdict="send", score=4, row_id="r1", body_hash="bh1"),
        Adjudication(email="a@x.example", verdict="drop", score=1, row_id="r2", body_hash="bh1"),
    ]
    assert unstable_bodies(same) == [], "identical bodies must not be flagged"

    different = [
        Adjudication(email="a@x.example", verdict="send", score=4, row_id="r1", body_hash="bh1"),
        Adjudication(email="a@x.example", verdict="drop", score=1, row_id="r2", body_hash="bh2"),
    ]
    assert unstable_bodies(different) == [("a@x.example", 1)]


def test_the_send_set_is_reported_as_a_range_not_a_point():
    from gtm_core.adjudication import send_set, tally

    t = tally(
        {
            "run-a": [
                Adjudication(email="a@x.example", verdict="send", score=4, row_id="r1"),
                Adjudication(email="b@x.example", verdict="send", score=4, row_id="r2"),
            ],
            "run-b": [
                Adjudication(email="a@x.example", verdict="drop", score=1, row_id="r3"),
                Adjudication(email="b@x.example", verdict="send", score=4, row_id="r4"),
            ],
        }
    )
    assert len(send_set(t, "consensus")) < len(send_set(t, "any"))
    assert send_set(t, "consensus") == ["b@x.example"]
    assert send_set(t, "any") == ["a@x.example", "b@x.example"]


def test_the_tally_metric_is_not_named_flip_rate(capsys):
    """`split_rate` must not collide with the different, pre-existing
    `gtm_core.eval_calibration.flip_rate` (rubric-order sensitivity, <10% bar)."""
    from gtm_core import eval_calibration
    from gtm_core.adjudication import Tally

    assert hasattr(Tally, "split_rate")
    assert not hasattr(Tally, "flip_rate")
    assert hasattr(eval_calibration, "flip_rate"), "the metric this must not collide with"


def test_tally_exits_nonzero_above_the_contested_threshold_but_still_writes_the_file(tmp_path):
    from gtm_core.adjudication import main

    def _write(path, records):
        import json

        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")

    run_a = tmp_path / "run-a.jsonl"
    run_b = tmp_path / "run-b.jsonl"
    _write(
        run_a,
        [
            {
                "email": "a@x.example",
                "verdict": "send",
                "score": 4,
                "row_id": "r1",
                "body_hash": "bh",
            }
        ],
    )
    _write(
        run_b,
        [
            {
                "email": "a@x.example",
                "verdict": "drop",
                "score": 1,
                "row_id": "r2",
                "body_hash": "bh",
            }
        ],
    )
    out = tmp_path / "tally.jsonl"
    rc = main(["tally", "--records", str(run_a), str(run_b), "--out", str(out)])
    assert rc == 1, "100% contested must fail the default 10% budget"
    assert out.exists(), "a failing tally must still be written — it is self-describing"

    out2 = tmp_path / "tally2.jsonl"
    rc2 = main(
        ["tally", "--records", str(run_a), str(run_b), "--out", str(out2), "--max-contested", "1.0"]
    )
    assert rc2 == 0, "raising the budget to 100% must let the same input pass"


def test_tally_creates_its_out_parent_directory(tmp_path):
    from gtm_core.adjudication import write_tally

    nested = tmp_path / "a" / "b" / "c.jsonl"
    write_tally([], nested)
    assert nested.exists()


def test_worst_verdict_keeps_the_first_tied_record_not_the_last():
    """The docstring used to claim ties keep the LAST record; Python's `max` only
    replaces on strictly-greater, so it actually keeps the FIRST. Pin the real
    behaviour so the docstring cannot drift from the code again."""
    from gtm_core.adjudication import worst_verdict

    first = Adjudication(email="a@x.example", verdict="drop", score=1, evidence="first")
    second = Adjudication(email="a@x.example", verdict="drop", score=1, evidence="second")
    assert worst_verdict([first, second]) is first


def test_body_hash_and_grounding_survive_a_round_trip(tmp_path):
    """`read_records` used to silently drop `grounding` on every round-trip even though
    `to_dict` emits it — any downstream body-identity or grounding check was vacuous."""
    from gtm_core.adjudication import read_records, write_records

    rec = Adjudication(
        email="a@x.example",
        verdict="send",
        score=4,
        row_id="r1",
        body_hash="bh1",
        grounding="clean",
    )
    path = tmp_path / "rec.jsonl"
    write_records([rec], path)
    [back] = read_records(path)
    assert back.body_hash == "bh1"
    assert back.grounding == "clean"


# --------------------------------------------------------- defect-class normalisation


def test_normalize_defect_class_folds_kebab_and_negated_spellings_onto_scope_vocab():
    """Every spelling the 2026-09-01 sweep produced lands on one canonical class."""
    from gtm_core.adjudication import DEFECT_SCOPE, defect_scope, normalize_defect_class

    for raw in (
        "fact_creates_problem",
        "fact-creates-problem",
        "Fact-Creates-No-Problem",
        "fact_does_not_create_problem",
    ):
        assert normalize_defect_class(raw) == "fact_earns_its_place", raw
    # Sibling criteria keep their names (never targeting) but are argument-scoped.
    assert normalize_defect_class("bridge-depends-on-fact") == "bridge_depends_on_fact"
    assert normalize_defect_class("fact_supports_pitch") == "fact_supports_pitch"
    assert defect_scope("bridge-depends-on-fact") == "argument"
    for raw in ("wrong-person", "wrong_seat", "seniority-mismatch"):
        assert normalize_defect_class(raw) == "right_person", raw
    for raw in ("frame-doesnt-fit-seat", "frame_misaligned"):
        assert normalize_defect_class(raw) == "frame_fits_seat", raw
    assert normalize_defect_class("wrong-entity-type") == "wrong_entity_type"
    assert defect_scope("wrong-entity-type") == "account"
    assert defect_scope("fact-creates-problem") == "argument"
    assert defect_scope("wrong-person") == "contact"
    assert set(DEFECT_SCOPE.values()) <= {"argument", "contact", "account", "data"}
    assert normalize_defect_class("") == "" and normalize_defect_class(None) == ""


def test_a_novel_class_is_normalised_but_scope_is_unknown():
    from gtm_core.adjudication import defect_scope, normalize_defect_class

    assert normalize_defect_class(" Proof-Unmapped ") == "proof_unmapped"
    assert defect_scope("proof-unmapped") == "unknown"


def test_unknown_scope_never_maps_to_account():
    """Fail-safe direction: a spelling nobody has seen must never become 'do not contact'."""
    from gtm_core.adjudication import defect_scope

    for raw in ("wrong-company-ish", "vendor", "account-bad", "competitor-maybe", "", None):
        assert defect_scope(raw) != "account", raw


def test_disposal_counts_kebab_targeting_drops_as_misfiled():
    """53 of ~65 misfiled drops were counted on 2026-09-01 because of spelling alone."""
    from gtm_core.adjudication import disposal_audit

    recs = [
        Adjudication(
            email="a@x.example", verdict="drop", score=1, defect_class="fact_creates_problem"
        ),
        Adjudication(
            email="b@x.example", verdict="drop", score=1, defect_class="fact-creates-problem"
        ),
        Adjudication(email="c@x.example", verdict="drop", score=1, defect_class="Wrong-Person"),
        Adjudication(
            email="d@x.example", verdict="drop", score=1, defect_class="wrong-entity-type"
        ),
    ]
    d = disposal_audit(recs, accounted_for=[])
    assert d.drop == 4
    assert d.misfiled_drop == 3, "kebab and negated spellings of a targeting class must count"


def test_write_verdicts_writes_normalised_defect_class(tmp_path):
    """The CSV carries the routing key; the free-text reason column is untouched."""
    import csv

    from gtm_core.adjudication import main, write_records

    csv_path = tmp_path / "list.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["email"])
        w.writeheader()
        w.writerow({"email": "a@acme.example"})
    records = tmp_path / "rec.jsonl"
    write_records(
        [
            Adjudication(
                email="a@acme.example",
                verdict="drop",
                score=1,
                defect_class="Fact-Creates-No-Problem",
                evidence="a phrase",
            )
        ],
        records,
    )
    out = tmp_path / "out.csv"
    rc = main(
        ["write-verdicts", "--csv", str(csv_path), "--records", str(records), "--out", str(out)]
    )
    assert rc == 0
    row = next(iter(csv.DictReader(out.open(encoding="utf-8"))))
    assert row["judge_defect_class"] == "fact_earns_its_place"
    assert row["judge_verdict_reason"] == "a phrase"


# --------------------------------------------------------- defect report, regen cap, require-qa


def test_defect_report_lists_top_classes_with_bounded_evidence_and_operator_guidance():
    from gtm_core.adjudication import defect_report

    recs = [
        Adjudication(
            email="a@x.example",
            verdict="re-angle",
            score=2,
            defect_class="fact-creates-problem",
            evidence="x" * 500,
        ),
        Adjudication(
            email="b@x.example",
            verdict="drop",
            score=1,
            defect_class="fact_creates_problem",
            note="the fact is a product description",
        ),
        Adjudication(email="c@x.example", verdict="send", score=4, defect_class="cta-overclaim"),
        Adjudication(email="d@x.example", verdict="re-angle", score=2, defect_class="wrong-person"),
    ]
    feedback = [
        {
            "trigger": "tier-a-generic",
            "decision": "generic",
            "note": "fine for these",
            "salvage_kind": "",
        },
        {
            "trigger": "judge-account-scope",
            "decision": "salvage",
            "salvage_kind": "different-argument:audit-trail",
            "note": "",
        },
    ]
    md = defect_report({"sweep-startup": recs}, known=["cta-overclaim"], feedback=feedback)
    assert "## sweep-startup" in md and "re-angle 2" in md and "send 1" in md
    assert "`fact_earns_its_place` · argument · 2" in md, (
        "spellings fold; a send row's class is not a defect"
    )
    assert "`right_person` · contact · 1" in md
    assert "x" * 121 not in md and "x" * 100 in md, "evidence is bounded"
    assert "## Operator guidance" in md and "tier-a-generic: generic ×1" in md
    assert "salvage asked for `different-argument:audit-trail` ×1" in md
    assert "fine for these" in md
    assert (
        "cta_overclaim" not in md.split("## Operator")[0].split("top defect")[1]
        or "INERT" not in md
    )


def test_defect_report_flags_inert_gates_and_novel_classes():
    from gtm_core.adjudication import defect_report

    recs = [
        Adjudication(email="a@x.example", verdict="drop", score=1, defect_class="cta-overclaim"),
        Adjudication(email="b@x.example", verdict="drop", score=1, defect_class="brand-new-thing"),
    ]
    md = defect_report({"s": recs}, known=["cta-overclaim"])
    assert "INERT GATES" in md and "cta_overclaim" in md
    assert "novel (no rule covers): brand_new_thing" in md


def test_regen_count_reads_the_chain_not_the_filename_and_refuses_at_the_cap(tmp_path):
    from gtm_core.adjudication import REGENERATION_CAP, main, regeneration_count

    v0 = tmp_path / "spec-a-2026-09-01.md"
    v0.write_text("```\nhook_cell: x\n```\nbody\n", encoding="utf-8")
    v1 = tmp_path / "spec-a-2026-09-02.md"
    v1.write_text("```\nregenerated_from: spec-a-2026-09-01.md\n```\nbody\n", encoding="utf-8")
    v2 = tmp_path / "renamed-2026-09-03.md"
    v2.write_text("```\nregenerated_from: spec-a-2026-09-02.md\n```\nbody\n", encoding="utf-8")
    v3 = tmp_path / "spec-a-2026-09-04.md"
    v3.write_text(
        "```\nregeneration: 3\nregenerated_from: renamed-2026-09-03.md\n```\nbody\n",
        encoding="utf-8",
    )
    assert (
        regeneration_count(v0) == 0 and regeneration_count(v1) == 1 and regeneration_count(v2) == 2
    )
    assert regeneration_count(v3) == 3 == REGENERATION_CAP
    assert main(["regen-count", "--spec", str(v2)]) == 0
    assert main(["regen-count", "--spec", str(v3)]) == 1, "the third regeneration is the last"


def test_require_qa_refuses_a_record_whose_spec_sha_moved(tmp_path):
    import hashlib

    from gtm_core.adjudication import main, require_qa

    spec = tmp_path / "spec-a-2026-09-01.md"
    spec.write_text("body v1\n", encoding="utf-8")
    qa = tmp_path / "qa"
    qa.mkdir()
    sha = hashlib.sha256(spec.read_bytes()).hexdigest()[:16]
    (qa / "seq-1.json").write_text(
        json.dumps({"spec": str(spec), "spec_sha256": sha, "verdict": "PASS", "errors": 0}),
        encoding="utf-8",
    )
    assert require_qa(spec, qa)[0] is True
    assert main(["require-qa", "--spec", str(spec), "--qa-dir", str(qa)]) == 0
    spec.write_text("body v2 — edited after the gate\n", encoding="utf-8")
    ok, why = require_qa(spec, qa)
    assert ok is False and "OTHER bytes" in why
    assert main(["require-qa", "--spec", str(spec), "--qa-dir", str(qa)]) == 1
    # A record for the current bytes that FAILED is still a refusal.
    sha2 = hashlib.sha256(spec.read_bytes()).hexdigest()[:16]
    (qa / "seq-2.json").write_text(
        json.dumps({"spec": str(spec), "spec_sha256": sha2, "verdict": "FAIL", "errors": 3}),
        encoding="utf-8",
    )
    assert require_qa(spec, qa)[0] is False


def test_defect_report_cli_refuses_an_output_path_outside_the_content_root(
    tmp_path, monkeypatch, capsys
):
    from gtm_core.adjudication import main, write_records

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    (tmp_path / "content" / "acme" / "prospects" / "evals").mkdir(parents=True)
    recs = tmp_path / "sweep-x-2026-09-01.jsonl"
    write_records(
        [
            Adjudication(
                email="a@x.example",
                verdict="drop",
                score=1,
                defect_class="wrong-person",
                evidence="a phrase",
            )
        ],
        recs,
    )
    outside = tmp_path / "elsewhere" / "report.md"
    assert main(["defect-report", "--records", str(recs), "--out", str(outside)]) == 2
    assert "REFUSED" in capsys.readouterr().err and not outside.exists()
    inside = tmp_path / "content" / "acme" / "prospects" / "evals" / "defect-report-2026-09-03.md"
    assert main(["defect-report", "--records", str(recs), "--out", str(inside)]) == 0
    assert "## sweep-x" in inside.read_text(encoding="utf-8")


def test_require_qa_is_keyed_on_the_spec_AND_the_csv(tmp_path):
    """A merge-render PASS is a claim about (spec x csv), so the gate must key on both.

    Both directions were live on 2026-09-10, from one pair of records sharing a spec hash:

    * a superseded FAIL against an old list blocked staging of the corrected list that
      passed, because the scan returned on the first spec-matching record it found;
    * far worse, without the csv a PASS earned against list A satisfied a staging of list B —
      the rows being exactly what a merge-render run checks.
    """
    import hashlib

    from gtm_core.adjudication import main, require_qa

    spec = tmp_path / "spec-a-2026-09-09.md"
    spec.write_text("body\n", encoding="utf-8")
    old_csv = tmp_path / "list-2026-09-09-send.csv"
    old_csv.write_text("email\na@one.example\nb@two.example\n", encoding="utf-8")
    new_csv = tmp_path / "list-2026-09-10-send.csv"
    new_csv.write_text("email\na@one.example\n", encoding="utf-8")
    qa = tmp_path / "qa"
    qa.mkdir()

    def sha(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]

    def rec(name, csv, verdict, errors):
        (qa / name).write_text(
            json.dumps(
                {
                    "spec": str(spec),
                    "spec_sha256": sha(spec),
                    "csv": str(csv),
                    "csv_sha256": sha(csv),
                    "verdict": verdict,
                    "errors": errors,
                }
            ),
            encoding="utf-8",
        )

    # Sorted order puts the superseded FAIL first — the shape that caused the false block.
    rec("seq-20260909.json", old_csv, "FAIL", 1)
    rec("seq-20260910.json", new_csv, "PASS", 0)

    ok, why = require_qa(spec, qa, new_csv)
    assert ok is True, f"the corrected list has its own PASS but was refused: {why}"
    assert (
        main(["require-qa", "--spec", str(spec), "--qa-dir", str(qa), "--csv", str(new_csv)]) == 0
    )

    # The old list still reports its own failure — the fix must not launder it.
    ok, why = require_qa(spec, qa, old_csv)
    assert ok is False and "not a PASS" in why

    # THE FAIL-OPEN: a PASS for new_csv must not clear a staging of some third list.
    other = tmp_path / "list-other-send.csv"
    other.write_text("email\nz@three.example\n", encoding="utf-8")
    ok, why = require_qa(spec, qa, other)
    assert ok is False and "OTHER bytes" in why, (
        "a QA record earned against a different list satisfied this one — that is the "
        "fail-open the --csv key exists to close."
    )

    # Spec-only stays answerable, but must NAME the list it actually verified so a caller
    # cannot read it as a check of theirs.
    ok, why = require_qa(spec, qa)
    assert ok is True and "did not verify your list" in why
