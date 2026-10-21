"""The judge's voice check — advisory, closed, and grounded in the email it is about.

All fixtures invented (docs/RULES.md R9) — no real recipient appears here.

What this locks in (operator decision 2026-10-06): the judge also lists phrases that read as
written by a template or a chatbot ("One thing I left out:", "Why work with us?", "no
worries"). Three properties make that list safe to record before anyone has labelled it:

* **It never moves a verdict.** The send gate reads ``verdict``; a flagged email that is
  otherwise a ``send`` stays a ``send``. A tone opinion that could hold a row would let an
  unmeasured judgment stop sends.
* **The kinds are a closed list.** A soft yes/no flag drifted between scoring batches on
  2026-09-11 (19/42 in one, 1/23 in the other); a named, closed vocabulary is the fix that
  memory recorded. An unknown kind is dropped, never coerced.
* **A quoted phrase must be IN the email.** The judge is told to quote exactly; a phrase that
  is not there is a flag the judge invented, and it is thrown out. The planted-phrase test
  below is the negative control.

``staged`` is ``None`` when the check produced nothing readable (no key, a non-list, an
unscored row, a record written before the field) and ``[]`` when it ran and found nothing.
The two must not collapse: an empty list reads as "clean", and that is a claim.
"""

from __future__ import annotations

import json

from agent.mcp.judge import rubric
from agent.mcp.judge.rubric import VOICE_KINDS, _batch_prompt, _prompt
from agent.mcp.judge.scoring import build_record, parse_verdict, staged_summary
from gtm_core.adjudication import Adjudication
from gtm_core.adjudication.cli import main as adjudication_main
from gtm_core.adjudication.io import read_records, write_records
from gtm_core.messaging import card

_ROW = {"email": "robin@brightpath.example", "title": "Head of Platform", "lane": "signal"}
_BODY = (
    "Hi Robin,\n\nOne thing I left out: agents that call third-party tools usually share a "
    "service account. Brightpath could give each one a scalable identity of its own.\n\n"
    "Want me to send the reference architecture?"
)


def _reply(**extra) -> str:
    base = {"verdict": "send", "score": 4, "defect_class": "", "evidence": "", "note": "ok"}
    return json.dumps(base | extra)


def _record(verdict: dict | None, body: str = _BODY) -> Adjudication:
    return build_record(
        _ROW,
        spec_path="spec.md",
        csv_path="rows.csv",
        subject="agent identity note",
        body=body,
        touch_n=1,
        verdict=verdict,
        backend="api",
        judge_batch=1,
        repair_attempt=0,
        repaired=False,
    )


# ------------------------------------------------------------------ what the judge is asked


def test_every_prompt_carries_the_voice_check_and_every_kind():
    single = _prompt("s", _BODY, {}, reverse=False, lane="signal")
    batch = _batch_prompt([("s", _BODY, {})], reverse=False, lane="signal")
    for prompt in (single, batch):
        assert rubric.VOICE_TEXT in prompt
        assert '"staged"' in prompt, "the reply shape must ask for the list"
        for kind in VOICE_KINDS:
            assert f"`{kind}`" in prompt


def test_the_voice_check_says_it_never_changes_the_verdict():
    assert "never changes the verdict" in rubric.VOICE_TEXT


def test_the_flip_control_reverses_the_rubric_not_the_voice_check():
    forward = _prompt("s", _BODY, {}, reverse=False, lane="signal")
    backward = _prompt("s", _BODY, {}, reverse=True, lane="signal")
    assert forward != backward, "the control still transforms the rubric"
    assert rubric.VOICE_TEXT in backward


# ------------------------------------------------------------------- reading the reply


def test_a_valid_flag_is_kept():
    parsed = parse_verdict(
        _reply(staged=[{"phrase": "One thing I left out:", "kind": "announced-point"}])
    )
    assert parsed["staged"] == [{"phrase": "One thing I left out:", "kind": "announced-point"}]


def test_flags_never_change_the_verdict_or_the_score():
    flags = [{"phrase": "scalable", "kind": "brochure-words"}] * 3
    parsed = parse_verdict(_reply(staged=flags))
    assert (parsed["verdict"], parsed["score"]) == ("send", 4)


def test_an_unknown_kind_is_dropped_not_coerced():
    parsed = parse_verdict(
        _reply(
            staged=[
                {"phrase": "scalable", "kind": "sounds-robotic"},
                {"phrase": "scalable", "kind": "brochure-words"},
            ]
        )
    )
    assert parsed["staged"] == [{"phrase": "scalable", "kind": "brochure-words"}]


def test_a_flag_with_no_phrase_is_dropped():
    parsed = parse_verdict(
        _reply(staged=[{"phrase": "  ", "kind": "padded-ask"}, {"kind": "padded-ask"}, "x"])
    )
    assert parsed["staged"] == []


def test_at_most_five_flags_are_kept():
    flags = [{"phrase": f"phrase {i}", "kind": "brochure-words"} for i in range(8)]
    assert len(parse_verdict(_reply(staged=flags))["staged"]) == 5


def test_a_missing_or_malformed_list_is_unknown_not_clean():
    assert parse_verdict(_reply())["staged"] is None
    assert parse_verdict(_reply(staged="lots"))["staged"] is None
    assert parse_verdict(_reply(staged=[]))["staged"] == []


def test_a_malformed_list_does_not_unscore_the_row():
    parsed = parse_verdict(_reply(staged={"phrase": "x"}))
    assert parsed is not None and parsed["verdict"] == "send"


# ------------------------------------------------------------- grounding in the email


def test_a_phrase_that_is_not_in_the_email_is_thrown_out():
    """Negative control: a planted phrase the email never contained must not survive."""
    parsed = parse_verdict(
        _reply(
            staged=[
                {"phrase": "Let that sink in.", "kind": "restating-closer"},
                {"phrase": "One thing I left out:", "kind": "announced-point"},
            ]
        )
    )
    assert _record(parsed).staged == [
        {"phrase": "One thing I left out:", "kind": "announced-point"}
    ]


def test_grounding_ignores_case_whitespace_and_curly_quotes():
    body = "Hi Robin,\n\nIt’s not just   identity, it’s policy."
    parsed = parse_verdict(
        _reply(staged=[{"phrase": "it's not just identity", "kind": "contrast-with-nobody"}])
    )
    assert len(_record(parsed, body=body).staged) == 1


def test_an_unscored_row_has_no_voice_answer():
    assert _record(None).staged is None


def test_staged_round_trips_and_keeps_none_distinct_from_empty(tmp_path):
    flagged = _record(
        parse_verdict(_reply(staged=[{"phrase": "scalable", "kind": "brochure-words"}]))
    )
    clean = _record(parse_verdict(_reply(staged=[])))
    unknown = _record(parse_verdict(_reply()))
    path = write_records([flagged, clean, unknown], tmp_path / "judged.jsonl")
    back = read_records(path)
    assert [r.staged for r in back] == [
        [{"phrase": "scalable", "kind": "brochure-words"}],
        [],
        None,
    ]


# ------------------------------------------------------------------ the instrument's id


def test_rubric_version_moves_when_the_voice_check_is_reworded(monkeypatch):
    before = rubric.rubric_version("signal")
    assert before != card.fingerprint(k for k, _ in rubric.rubric_for("signal")), (
        "a prompt that now carries the voice check is a different instrument"
    )
    monkeypatch.setattr(rubric, "VOICE_TEXT", rubric.VOICE_TEXT + " Also count emoji.")
    assert rubric.rubric_version("signal") != before


# ------------------------------------------------------------- where the operator sees it


def test_the_summary_counts_checked_and_flagged_rows_by_kind():
    flagged = _record(
        parse_verdict(
            _reply(
                staged=[
                    {"phrase": "One thing I left out:", "kind": "announced-point"},
                    {"phrase": "scalable", "kind": "brochure-words"},
                ]
            )
        )
    )
    clean = _record(parse_verdict(_reply(staged=[])))
    unknown = _record(parse_verdict(_reply()))
    assert staged_summary([flagged, clean, unknown]) == {
        "rows_checked": 2,
        "rows_flagged": 1,
        "by_kind": {"announced-point": 1, "brochure-words": 1},
    }


def test_rank_prints_the_flagged_phrases(tmp_path, capsys):
    rec = _record(
        parse_verdict(
            _reply(staged=[{"phrase": "One thing I left out:", "kind": "announced-point"}])
        )
    )
    path = write_records([rec], tmp_path / "judged.jsonl")
    assert adjudication_main(["rank", "--records", str(path)]) == 0
    out = capsys.readouterr().out
    assert 'announced-point "One thing I left out:"' in out


def test_the_run_result_reports_the_voice_summary(monkeypatch, tmp_path):
    """The caller sees the voice check in the run result, beside the verdict tally — the
    flags are only useful if the person reading the run is told they exist."""
    import asyncio

    from agent.mcp.judge import server

    flagged = _record(
        parse_verdict(
            _reply(staged=[{"phrase": "One thing I left out:", "kind": "announced-point"}])
        )
    )

    async def _runner(rows, touch, **kw):
        return [flagged], ""

    monkeypatch.setattr(server, "select_backend", lambda: ("sdk", "test"))
    monkeypatch.setattr(server, "load_rows", lambda *a: ([_ROW], [1], []))
    monkeypatch.setattr(server, "confine_to_content_root", lambda p: (tmp_path / "out.jsonl", ""))
    monkeypatch.setattr(server, "_score_sdk", _runner)
    monkeypatch.setattr(server, "judge_context", lambda *a: {})
    payload = json.loads(asyncio.run(server.score_emails("spec.md", "rows.csv", "out.jsonl")))
    assert payload["verdicts"] == {"send": 1}, "the voice check left the verdict alone"
    assert payload["voice"] == {
        "rows_checked": 1,
        "rows_flagged": 1,
        "by_kind": {"announced-point": 1},
    }


# ----------------------------------------------------------------- added on review


def test_a_phrase_must_match_whole_words_not_part_of_one():
    """ "scalable" is not in an email that says "unscalable" — a partial-word match would flag
    a word the sender never wrote."""
    body = "Hi Robin,\n\nShared service accounts get unscalable fast."
    parsed = parse_verdict(_reply(staged=[{"phrase": "scalable", "kind": "brochure-words"}]))
    assert _record(parsed, body=body).staged == []


def test_the_batched_transport_carries_the_voice_answer_per_email():
    from agent.mcp.judge.scoring import parse_verdict_array

    reply = json.dumps(
        [
            json.loads(_reply(staged=[{"phrase": "scalable", "kind": "brochure-words"}])),
            json.loads(_reply(staged=[])),
        ]
    )
    first, second = parse_verdict_array(reply, 2)
    assert first["staged"] == [{"phrase": "scalable", "kind": "brochure-words"}]
    assert second["staged"] == []


def test_a_hand_edited_record_with_junk_flags_reads_back_without_them(tmp_path):
    path = tmp_path / "judged.jsonl"
    line = {"email": "robin@brightpath.example", "verdict": "send", "score": 4}
    line["staged"] = ["junk", {"phrase": 7, "kind": "brochure-words"}, {"phrase": "scalable"}]
    line2 = dict(line, staged=[{"phrase": "scalable", "kind": "brochure-words"}, "junk"])
    path.write_text(json.dumps(line) + "\n" + json.dumps(line2) + "\n", encoding="utf-8")
    first, second = read_records(path)
    assert first.staged == []
    assert second.staged == [{"phrase": "scalable", "kind": "brochure-words"}]
    assert adjudication_main(["rank", "--records", str(path)]) == 0, "rank must not crash on it"


def test_the_send_gate_columns_are_identical_with_or_without_flags(tmp_path):
    """The hard requirement, checked where the gate reads it: `write-verdicts` produces the
    CSV `account_integrity --require-verdict send` admits rows from. A flagged send and an
    unflagged send must write the same judge columns, and no voice column is added."""
    import csv

    csv_path = tmp_path / "list.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["email"])
        w.writeheader()
        w.writerow({"email": _ROW["email"]})

    def _written(staged) -> dict:
        rec = _record(parse_verdict(_reply(staged=staged)))
        records = write_records([rec], tmp_path / "rec.jsonl")
        out = tmp_path / "out.csv"
        args = ["write-verdicts", "--csv", str(csv_path), "--records", str(records)]
        assert adjudication_main([*args, "--out", str(out)]) == 0
        return next(iter(csv.DictReader(out.open(encoding="utf-8"))))

    flagged = _written([{"phrase": "One thing I left out:", "kind": "announced-point"}])
    clean = _written([])
    assert flagged == clean
    assert flagged["judge_verdict"] == "send"
