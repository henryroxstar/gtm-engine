"""Tests for `python -m gtm_core.prospects status` (PS8).

Follows the env-redirection pattern `tests/test_lanes.py`'s CLI tests use:
`GTM_CONTENT_ROOT` points the whole pipeline at a `tmp_path` tree, and the state files
this CLI reads (`lanes-state.jsonl`, `latest.json`) are seeded directly rather than
produced by a real `lanes route` run.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from gtm_core import prospect_status_cli as cli


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _write_latest(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"kind": "prospects", "items": items}), encoding="utf-8")


def _state_rows() -> list[dict]:
    return [
        {"email": "a@x.example", "lane": "generic", "reason": ""},
        {"email": "b@x.example", "lane": "generic", "reason": ""},
        {"email": "c@x.example", "lane": "repair", "reason": "tier-a-generic"},
        # old/unmigrated record: no "reason" key at all, only "trigger" — the CLI must
        # fall back to it.
        {"email": "d@x.example", "lane": "hold", "trigger": "tier-a-generic"},
        {"email": "e@x.example", "lane": "hold", "reason": "researcher-drop"},
        {"email": "f@x.example", "lane": "excluded", "reason": "already-enrolled"},
        {"email": "g@x.example", "lane": "excluded", "reason": "competitor-direct"},
        {"email": "h@x.example", "lane": "personalised", "reason": ""},
    ]


def _latest_items() -> list[dict]:
    return [
        {"contact_name": "Jordan Vance", "status": "new"},
        {"contact_name": "Alex Kim", "contact_email": "alex@x.example", "status": "new"},
        {"contact_name": "", "status": "new"},
        {"contact_name": "Sam Lee", "status": "disqualified"},
        {"contact_name": "Robin Chen", "status": "new"},
    ]


def _seed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    _write_jsonl(tmp_path / "acme" / "prospects" / "evals" / "lanes-state.jsonl", _state_rows())
    _write_latest(tmp_path / "acme" / "prospects" / "latest.json", _latest_items())


def _counts_from_output(out: str) -> dict[str, int]:
    """Pull the first digit run off each status line — every NEXT_STEP caption is
    digit-free, so the first match on a status's line is always its count regardless of
    the exact column spacing (a formatting choice this test does not pin)."""
    counts = {}
    for label, key in [
        ("Waiting on you", "waiting_on_you"),
        ("Sorted — not yet checked", "ready_to_send"),
        ("Being reworked", "being_fixed"),
        ("In the sending tool", "in_sending_tool"),
        ("Closed — not contacting", "not_emailing"),
        ("Still finding the right person", "needs_address"),
    ]:
        line = next(line for line in out.splitlines() if line.startswith(label))
        counts[key] = int(re.search(r"\d+", line).group())
    total_line = next(
        line for line in out.splitlines() if "every person in the current list" in line
    )
    counts["total"] = int(re.search(r"\d+", total_line).group())
    return counts


def test_no_state_file_exits_1_with_a_clear_message(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    assert cli.main(["--profile", "acme"]) == 1
    out = capsys.readouterr().out
    assert "acme" not in out  # the message names no internal path/profile detail
    assert "prospecting" in out.lower()


def test_no_state_file_message_avoids_banned_operator_vocabulary():
    """The whole point of PS8 is shielding the operator from internal pipeline
    vocabulary (`lane`/`lanes` among it) — this message is on the exit-1 path and
    is never scanned by `tests/lint/operator_vocabulary.py` itself (that lint only
    scans `prospect_status.LABELS`/`NEXT_STEP` plus whatever a caller explicitly
    passes it), so reuse its own scanner directly on the string rather than trust a
    hand-rolled substring check to catch the same class of bug it exists for."""
    import sys
    from pathlib import Path

    lint_dir = Path(__file__).resolve().parent / "lint"
    if str(lint_dir) not in sys.path:
        sys.path.insert(0, str(lint_dir))
    import operator_vocabulary as ov

    hits = [h for h in ov.findings(text=cli._NO_ROUTE_MESSAGE) if h[0] == "<text>"]
    assert hits == []


def test_default_report_computes_the_six_way_split_and_needs_address(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch)
    assert cli.main(["--profile", "acme"]) == 0
    out = capsys.readouterr().out
    counts = _counts_from_output(out)
    assert counts["waiting_on_you"] == 1  # hold/tier-a-generic (via trigger fallback)
    assert counts["ready_to_send"] == 3  # 2 generic + 1 personalised
    assert counts["being_fixed"] == 1  # repair
    assert counts["in_sending_tool"] == 1  # excluded/already-enrolled
    assert counts["not_emailing"] == 2  # excluded/competitor-direct + hold/researcher-drop
    assert counts["total"] == 8
    assert counts["needs_address"] == 2  # Jordan Vance + Robin Chen
    assert "Nothing sends until you start a sequence in the sending tool." in out


def test_output_is_byte_identical_across_two_runs_on_unchanged_data(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch)
    assert cli.main(["--profile", "acme"]) == 0
    first = capsys.readouterr().out
    assert cli.main(["--profile", "acme"]) == 0
    second = capsys.readouterr().out
    assert first == second


def test_why_waiting_on_you_groups_by_question_with_counts(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    rows = [
        {"email": "a@x.example", "lane": "hold", "reason": "tier-a-generic"},
        {"email": "b@x.example", "lane": "hold", "reason": "tier-a-generic"},
        {"email": "c@x.example", "lane": "hold", "reason": "duplicate-contact"},
        {"email": "d@x.example", "lane": "hold", "reason": "prior-contact"},
        {"email": "e@x.example", "lane": "hold", "reason": "engaged-account"},
    ]
    _write_jsonl(tmp_path / "acme" / "prospects" / "evals" / "lanes-state.jsonl", rows)
    assert cli.main(["--profile", "acme", "--why", "waiting_on_you"]) == 0
    out = capsys.readouterr().out
    assert "Waiting on you — 5 — by question:" in out
    lines = out.splitlines()
    assert any(
        "2" in line and "A Tier-A account would get the standard email" in line for line in lines
    )
    assert any("2" in line and "already a relationship signal" in line for line in lines)
    assert any("1" in line and "second person" in line for line in lines)


def test_why_non_question_status_groups_by_reason(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    rows = [
        {"email": "a@x.example", "lane": "excluded", "reason": "competitor-direct"},
        {"email": "b@x.example", "lane": "excluded", "reason": "competitor-direct"},
        {"email": "c@x.example", "lane": "hold", "reason": "researcher-drop"},
    ]
    _write_jsonl(tmp_path / "acme" / "prospects" / "evals" / "lanes-state.jsonl", rows)
    assert cli.main(["--profile", "acme", "--why", "not_emailing"]) == 0
    out = capsys.readouterr().out
    assert "Closed — not contacting — 3 — by reason:" in out
    lines = out.splitlines()
    assert any("2" in line and "competitor-direct" in line for line in lines)
    assert any("1" in line and "researcher-drop" in line for line in lines)


_GOOD_LINE = '{"email": "a@x.example", "lane": "generic", "reason": ""}\n'


@pytest.mark.parametrize("bad", ["not valid json", "null", "123", '["a@x.example"]'])
@pytest.mark.parametrize("why", [[], ["--why", "ready_to_send"]])
def test_a_state_line_that_is_not_a_record_is_refused_not_skipped(
    tmp_path, monkeypatch, capsys, bad, why
):
    """Changed 2026-09-21 (review L2): these lines used to be skipped and the block printed
    exit 0 with one person fewer — a confident undercount pasted to the operator, while
    `lanes route` refused the very same file. Now the two agree."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    state_file = tmp_path / "acme" / "prospects" / "evals" / "lanes-state.jsonl"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(_GOOD_LINE + "\n" + bad + "\n", encoding="utf-8")
    _write_latest(tmp_path / "acme" / "prospects" / "latest.json", [])
    assert cli.main(["--profile", "acme", *why]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("The sorted list could not be read")
    assert "line 3" in captured.err and len(captured.err.strip().splitlines()) == 1


def test_blank_lines_in_the_state_file_are_not_a_defect(tmp_path, monkeypatch, capsys):
    """Negative control for the refusal above."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    state_file = tmp_path / "acme" / "prospects" / "evals" / "lanes-state.jsonl"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(_GOOD_LINE + "\n   \n" + _GOOD_LINE, encoding="utf-8")
    _write_latest(tmp_path / "acme" / "prospects" / "latest.json", [])
    assert cli.main(["--profile", "acme"]) == 0
    assert _counts_from_output(capsys.readouterr().out)["total"] == 2


@pytest.mark.parametrize("which", ["lanes-state.jsonl", "latest.json"])
def test_bytes_that_are_not_text_are_one_clear_line(tmp_path, monkeypatch, capsys, which):
    _seed(tmp_path, monkeypatch)
    target = next((tmp_path / "acme" / "prospects").rglob(which))
    target.write_bytes(target.read_bytes() + b"\xff\xfe\n")
    assert cli.main(["--profile", "acme"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "Traceback" not in captured.err
    assert len(captured.err.strip().splitlines()) == 1


def test_why_flag_does_not_print_the_default_block(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch)
    assert cli.main(["--profile", "acme", "--why", "not_emailing"]) == 0
    out = capsys.readouterr().out
    assert "PAUSED" not in out
    assert "every person in the current list" not in out


def test_an_unmapped_reason_is_counted_visibly_never_a_traceback(tmp_path, monkeypatch, capsys):
    """Changed 2026-09-21 (PSK-028): this used to REQUIRE `UnmappedStatus` to propagate. The
    status step is mandatory in every run and pasted to the operator, so one state record this
    build cannot map must show up as a counted line, not end the run in a traceback."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    rows = [
        {"email": "z@x.example", "lane": "hold", "reason": "not-a-real-trigger"},
        {"email": "y@x.example", "lane": "generic", "reason": "no-judge-verdict"},
    ]
    _write_jsonl(tmp_path / "acme" / "prospects" / "evals" / "lanes-state.jsonl", rows)
    assert cli.main(["--profile", "acme"]) == 0
    captured = capsys.readouterr()
    line = next(ln for ln in captured.out.splitlines() if ln.startswith("Unrecognised"))
    assert re.search(r"^Unrecognised\s+1\b", line) and "sort the list again" in line
    assert _counts_from_output(captured.out)["total"] == 2  # it still participates in the sum
    # the command that fixes it goes to stderr — the table itself stays in the operator's words
    assert "not recognise" in captured.err and "re-run `lanes route`" in captured.err
    assert cli.main(["--profile", "acme", "--why", "waiting_on_you"]) == 0


def test_no_unrecognised_line_when_every_record_maps(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch)
    assert cli.main(["--profile", "acme"]) == 0
    captured = capsys.readouterr()
    assert "Unrecognised" not in captured.out and captured.err == ""
