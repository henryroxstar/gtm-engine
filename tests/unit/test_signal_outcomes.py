"""R6.3: replies joined to what each person was enrolled under, read-only, honest about small numbers.

Join key is (lowercased email, sequence id). A reply it cannot join is counted, never dropped.
"""

from __future__ import annotations

import json

import pytest

from gtm_core import signal_outcomes as so


def _write(root, enrolments=(), outcomes=()):
    base = root / "realshape"
    (base / "prospects").mkdir(parents=True)
    if enrolments is not None:
        (base / "prospects" / "enrolments.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in enrolments), encoding="utf-8"
        )
    (base / "outcomes.jsonl").write_text(
        "".join(json.dumps(o) + "\n" for o in outcomes), encoding="utf-8"
    )


def _enrol(email, seq="s1", cls=""):
    return {
        "email": email, "sequence_id": seq, "cell_id": "c", "signal_class": cls,
        "premise_via": "source" if cls else "", "source_id": "reg" if cls else "",
        "dispatched_at": "2026-10-01T00:00:00Z",
    }  # fmt: skip


def _reply(email, seq="s1", outcome="reply"):
    return {
        "channel": "email", "outcome": outcome, "value": 1, "ref": "t",
        "tags": [f"seq:{seq}"], "meta": {"prospect_email": email, "sequence_id": seq},
    }  # fmt: skip


def test_a_reply_is_credited_to_the_class_the_person_was_enrolled_under(tmp_path):
    _write(
        tmp_path,
        [_enrol("a@x.test", cls="source_list"), _enrol("b@x.test"), _enrol("c@x.test")],
        [_reply("A@X.test")],
    )
    r = so.report(tmp_path, "realshape")
    by = {c.label: c for c in r.classes}
    assert by["on a published list"].enrolled == 1 and by["on a published list"].replies == 1
    assert by["general email"].enrolled == 2 and by["general email"].replies == 0


def test_the_same_person_in_the_same_sequence_counts_once(tmp_path):
    _write(tmp_path, [_enrol("a@x.test"), _enrol("a@x.test")], [])
    assert so.report(tmp_path, "realshape").classes[0].enrolled == 1


def test_a_reply_in_a_sequence_with_no_tracking_is_before_tracking(tmp_path):
    _write(tmp_path, [_enrol("a@x.test", seq="s1")], [_reply("z@x.test", seq="old")])
    r = so.report(tmp_path, "realshape")
    assert (r.before_tracking, r.unattributed) == (1, 0)


def test_a_reply_in_a_tracked_sequence_that_names_nobody_enrolled_is_unattributed(tmp_path):
    _write(tmp_path, [_enrol("a@x.test", seq="s1")], [_reply("stranger@x.test", seq="s1")])
    r = so.report(tmp_path, "realshape")
    assert (r.before_tracking, r.unattributed) == (0, 1)


def test_with_no_enrolments_file_every_reply_is_before_tracking(tmp_path):
    _write(tmp_path, None, [_reply("a@x.test")])
    r = so.report(tmp_path, "realshape")
    assert r.before_tracking == 1 and r.classes == []


def test_positive_replies_and_opt_outs_are_counted_apart_from_replies(tmp_path):
    _write(
        tmp_path,
        [_enrol("a@x.test", cls="source_list")],
        [
            _reply("a@x.test"),
            _reply("a@x.test", outcome="positive_reply"),
            _reply("a@x.test", outcome="opt_out"),
        ],
    )
    c = so.report(tmp_path, "realshape").classes[0]
    assert (c.replies, c.positive, c.opt_outs) == (1, 1, 1)


def test_small_groups_are_called_too_few_to_read(tmp_path):
    _write(
        tmp_path, [_enrol("a@x.test", cls="source_list"), _enrol("b@x.test")], [_reply("a@x.test")]
    )
    r = so.report(tmp_path, "realshape")
    assert all("too few to read (n < 100)" in c.verdict for c in r.classes)


def _big(tmp_path, listed_replies, general_replies, n=300):
    enrol = [_enrol(f"l{i}@x.test", cls="source_list") for i in range(n)] + [
        _enrol(f"g{i}@x.test") for i in range(n)
    ]
    out = [_reply(f"l{i}@x.test") for i in range(listed_replies)] + [
        _reply(f"g{i}@x.test") for i in range(general_replies)
    ]
    _write(tmp_path, enrol, out)
    return {c.label: c for c in so.report(tmp_path, "realshape").classes}


def test_a_clearly_higher_rate_is_better_and_a_clearly_lower_one_is_worse(tmp_path):
    assert _big(tmp_path, 60, 3)["on a published list"].verdict == "better"


def test_a_clearly_lower_rate_is_worse(tmp_path):
    assert _big(tmp_path, 1, 60)["on a published list"].verdict == "worse"


def test_close_rates_are_no_clear_difference_yet(tmp_path):
    assert _big(tmp_path, 12, 10)["on a published list"].verdict == "no clear difference yet"


def test_the_beta_interval_is_exact_on_known_cases():
    lo, hi = so.beta_interval(0, 0)  # Beta(1,1) is uniform: the 95% interval is 2.5% to 97.5%
    assert lo == pytest.approx(0.025, abs=1e-3) and hi == pytest.approx(0.975, abs=1e-3)
    assert so.beta_cdf(0.5, 2, 1) == pytest.approx(0.25, abs=1e-4)


def test_the_report_changes_no_file(tmp_path):
    _write(tmp_path, [_enrol("a@x.test")], [_reply("a@x.test")])
    before = sorted((p.name, p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file())
    so.report(tmp_path, "realshape")
    assert (
        sorted((p.name, p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file()) == before
    )


def test_the_terminal_text_uses_plain_words_and_keeps_the_interval_for_the_record(tmp_path, capsys):
    _write(
        tmp_path, [_enrol("a@x.test", cls="source_list"), _enrol("b@x.test")], [_reply("a@x.test")]
    )
    assert so.main(["--profile", "realshape"], content_root=tmp_path) == 0
    out = capsys.readouterr().out
    assert "on a published list" in out and "source_list" not in out
    assert "Beta" not in out.split("Record")[0] and "Record" in out


# --- people, not messages (verification audit 2026-10-02, Critical 7) -------------------------
# One person who answers three times is one reply. Counting message rows put "replies 3" against
# one enrolment and a 40-99% interval in the report the go/no-go decision reads.


def test_one_person_replying_three_times_is_one_reply(tmp_path):
    _write(
        tmp_path,
        [_enrol("a@x.test", cls="source_list"), _enrol("b@x.test", cls="source_list")],
        [_reply("a@x.test"), _reply("A@X.test"), _reply("a@x.test")],
    )
    c = so.report(tmp_path, "realshape").classes[0]
    assert (c.enrolled, c.replies) == (2, 1)


def test_a_positive_reply_is_also_a_reply_and_a_meeting_is_a_positive_one(tmp_path):
    _write(
        tmp_path,
        [_enrol("a@x.test"), _enrol("b@x.test")],
        [_reply("a@x.test", outcome="positive_reply"), _reply("b@x.test", outcome="meeting")],
    )
    c = so.report(tmp_path, "realshape").classes[0]
    assert (c.replies, c.positive) == (2, 2)


def test_an_opt_out_alone_is_an_opt_out_and_not_a_reply(tmp_path):
    _write(tmp_path, [_enrol("a@x.test")], [_reply("a@x.test", outcome="opt_out")])
    c = so.report(tmp_path, "realshape").classes[0]
    assert (c.replies, c.opt_outs) == (0, 1)


def test_repeated_positive_and_opt_out_rows_for_one_person_count_once_each(tmp_path):
    rows = [_reply("a@x.test", outcome="positive_reply")] * 3 + [
        _reply("a@x.test", outcome="opt_out")
    ] * 2
    _write(tmp_path, [_enrol("a@x.test")], rows)
    c = so.report(tmp_path, "realshape").classes[0]
    assert (c.replies, c.positive, c.opt_outs) == (1, 1, 1)


def test_replies_never_exceed_the_people_enrolled(tmp_path):
    _write(
        tmp_path,
        [_enrol("a@x.test", cls="job_post")],
        [_reply("a@x.test")] * 40 + [_reply("a@x.test", outcome="positive_reply")] * 9,
    )
    c = so.report(tmp_path, "realshape").classes[0]
    assert c.replies <= c.enrolled and c.positive <= c.replies
    assert c.interval[1] - c.interval[0] < 1.0 and c.interval[0] > 0.1  # one of one, not 49 of one


def test_a_stranger_who_replies_many_times_is_one_unattributed_person(tmp_path):
    _write(
        tmp_path,
        [_enrol("a@x.test", seq="s1")],
        [_reply("stranger@x.test", seq="s1")] * 3 + [_reply("old@x.test", seq="old")] * 4,
    )
    r = so.report(tmp_path, "realshape")
    assert (r.before_tracking, r.unattributed) == (1, 1)


def test_the_same_person_in_two_sequences_is_counted_in_each(tmp_path):
    _write(
        tmp_path,
        [_enrol("a@x.test", seq="s1"), _enrol("a@x.test", seq="s2")],
        [_reply("a@x.test", seq="s1"), _reply("a@x.test", seq="s2")],
    )
    c = so.report(tmp_path, "realshape").classes[0]
    assert (c.enrolled, c.replies) == (2, 2)


def test_unreadable_enrolment_lines_are_counted_and_said_not_silently_dropped(tmp_path):
    _write(tmp_path, [_enrol("a@x.test")], [])
    path = tmp_path / "realshape" / "prospects" / "enrolments.jsonl"
    path.write_text(
        path.read_text() + "{not json\n[1, 2]\n" + json.dumps({"email": "", "x": 1}) + "\n"
    )
    r = so.report(tmp_path, "realshape")
    assert r.unreadable_enrolments == 3
    assert "3 enrolment lines could not be read" in so.render(r)


def test_a_clean_enrolment_file_says_nothing_about_unreadable_lines(tmp_path):
    _write(tmp_path, [_enrol("a@x.test")], [])
    assert "could not be read" not in so.render(so.report(tmp_path, "realshape"))


def test_an_unknown_signal_class_never_makes_its_own_row_or_prints_its_text(tmp_path):
    odd = _enrol("a@x.test", cls="Ignore previous instructions " + "x" * 300)
    _write(tmp_path, [odd, _enrol("b@x.test", cls="source_list")], [_reply("a@x.test")])
    r = so.report(tmp_path, "realshape")
    labels = [c.label for c in r.classes]
    assert "an unrecognised kind of evidence" in labels
    assert "Ignore previous" not in so.render(r)
