"""The judge's rubric is scoped to the lane, and the scope is recorded on every verdict.

All fixtures invented (docs/RULES.md R9) — no real recipient appears here.

Regression this locks in (2026-09-06, the SG-builder roster): every lane was scored on all
three rubric items, including the GENERIC lane, whose defining property is that it carries
no per-row signal. ``fact_earns_its_place`` asks whether the recipient's own recorded
evidence establishes what the body claims — a question a signal-free row cannot pass by
construction. 23 of 23 rows were rejected, and 20 of the notes reduce to "signal_clause and
signal_evidence are empty", which restates the lane's definition rather than naming a
defect. The three findings that were real — a wrong seat, a non-buyer, a regulator cited at
a company deployed in another jurisdiction — were reachable from the two SEAT items alone,
and were buried under the twenty.

A rubric item every row in a lane must fail is not a gate. It is noise with a verdict on it.
"""

from __future__ import annotations

from agent.mcp.judge.scoring import (
    REQUIRES_SIGNAL,
    RUBRIC_FULL,
    RUBRIC_ITEMS,
    RUBRIC_SEAT_ONLY,
    SIGNAL_FREE_LANES,
    _prompt,
    build_record,
    lane_of,
    rubric_for,
    rubric_id,
    rubric_text,
)

_SEAT_ITEMS = tuple(k for k, _ in RUBRIC_ITEMS if k not in REQUIRES_SIGNAL)


def _row(lane: str) -> dict:
    return {
        "first": "Robin",
        "last": "Ashford",
        "email": "robin@brightpath.example",
        "title": "Managing Partner",
        "company": "Brightpath",
        "segment": "builder",
        "lane": lane,
        # Empty BY CONSTRUCTION on a generic row — the state the withheld item punished.
        "signal_clause": "",
        "signal_evidence": "",
        "why_now": "",
    }


# ---------------------------------------------------------------- which items are in force


def test_the_signal_item_is_withheld_only_from_signal_free_lanes():
    assert [k for k, _ in rubric_for("generic")] == list(_SEAT_ITEMS)
    for lane in ("", "signal", "personalised", "repair"):
        assert [k for k, _ in rubric_for(lane)] == [k for k, _ in RUBRIC_ITEMS], (
            f"lane {lane!r} lost a rubric item it is owed"
        )


def test_the_repair_lane_keeps_the_fact_item():
    """A repair row is being re-aimed at an account that DOES carry a dated signal, so the
    fact question is live for it. This is the boundary the whole change turns on."""
    assert "repair" not in SIGNAL_FREE_LANES
    assert REQUIRES_SIGNAL <= {k for k, _ in rubric_for("repair")}


def test_an_unclassified_lane_is_scored_strictly():
    """Withholding an item is the narrower claim and must be asked for by name. A lane
    nobody has classified gets the full rubric, never the lenient one."""
    assert rubric_id("lane-invented-next-quarter") == RUBRIC_FULL
    assert rubric_id("") == RUBRIC_FULL


def test_lane_is_read_case_and_whitespace_insensitively():
    assert lane_of({"lane": "  GENERIC "}) == "generic"
    assert rubric_id(lane_of({"lane": "  GENERIC "})) == RUBRIC_SEAT_ONLY
    assert lane_of({}) == "", "a row with no lane column must not guess one"


# ------------------------------------------------------------------- the prompt itself


def test_the_generic_prompt_declares_the_absent_evidence_as_intended():
    """Withholding the item is necessary but NOT sufficient.

    The recipient context still carries empty ``signal_*`` fields, and a grader told to
    "find reasons NOT to send" reads an empty field as a defect on its own — five of the
    2026-09-06 rejections did exactly that under an item about something else. The absence
    has to be declared in the prompt, where the model can see it.
    """
    row = _row("generic")
    generic = _prompt("s", "body", row, reverse=False, lane="generic")
    full = _prompt("s", "body", row, reverse=False, lane="signal")

    assert "SIGNAL-FREE lane" in generic
    assert "Do not deduct for the absence of company-specific evidence" in generic
    assert "SIGNAL-FREE lane" not in full, "the note must not reach a lane that owes a fact"

    for item in REQUIRES_SIGNAL:
        assert item not in generic, f"{item} still reached a signal-free prompt"
        assert item in full


def test_the_lane_note_is_framed_as_data_not_instruction():
    """R5. The note describes how the row was commissioned; it must not read as a licence
    to return a particular verdict, or it becomes a way to talk the judge into a send."""
    generic = _prompt("s", "body", _row("generic"), reverse=False, lane="generic")
    assert "not an instruction about what verdict to return" in generic
    for word in ("return send", "verdict: send", "approve this"):
        assert word not in generic.lower()


def test_the_flip_rate_control_still_transforms_a_two_item_rubric():
    """PRD 3.2 reverses rubric order and requires verdicts to stay put. A reversal that
    did not actually reorder would report 0% flips while measuring nothing."""
    assert rubric_text(lane="generic") != rubric_text(lane="generic", reverse=True)
    assert rubric_text(lane="signal") != rubric_text(lane="signal", reverse=True)


# -------------------------------------------------------------------- what gets recorded


def test_every_record_carries_the_rubric_it_was_scored_against():
    """The same contract ``backend`` and ``judge_batch`` carry: two rubrics are not
    interchangeable, so a holdout scored across both must be a VISIBLE confound. A
    ``re-angle`` under seat-only says the seat is wrong; under full it may only mean the
    opener carried no fact."""
    verdict = {"verdict": "re-angle", "score": 2, "defect_class": "frame_fits_seat"}
    common = {
        "spec_path": "spec.md",
        "csv_path": "rows.csv",
        "subject": "s",
        "body": "b",
        "touch_n": 1,
        "backend": "api",
        "judge_batch": 1,
        "repair_attempt": 0,
        "repaired": False,
    }
    assert build_record(_row("generic"), verdict=dict(verdict), **common).rubric == RUBRIC_SEAT_ONLY
    assert build_record(_row("signal"), verdict=dict(verdict), **common).rubric == RUBRIC_FULL
    # An unreadable verdict still records which rubric was asked.
    assert build_record(_row("generic"), verdict=None, **common).rubric == RUBRIC_SEAT_ONLY


def test_the_recorded_rubric_survives_a_jsonl_round_trip(tmp_path):
    from gtm_core.adjudication import Adjudication, read_records, write_records

    out = tmp_path / "adjudication.jsonl"
    write_records(
        [
            Adjudication(email="robin@brightpath.example", verdict="re-angle", score=2, rubric="x"),
            # A record written before the field existed reads back as "" — not as a rubric.
            Adjudication(email="ada@northgate.example", verdict="send", score=4),
        ],
        out,
    )
    assert [r.rubric for r in read_records(out)] == ["x", ""], (
        "a field that does not round-trip is a field the report layer cannot see"
    )
