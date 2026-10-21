"""MM2/MM4: a tenant's budget read must refuse when it cannot be trusted — and only then.

``sum_month_costs`` is tolerant BY DESIGN (an unparseable line is skipped, pinned by
``tests/contracts/test_metering.py``): on the single-tenant VPS a corrupt append must not make the
guard unreadable. On the multi-tenant backend the same tolerance is a hole — a skipped line is spend
that vanishes from the cap. ``strict=True`` is the tenant posture:

  - an unreadable ledger or an INTERIOR corrupt line raises (the caller refuses);
  - a torn FINAL line is tolerated: a worker killed mid-append must not brick a tenant's budget;
  - a row whose cost cannot be read as a number raises — it is spend we cannot price.

The default stays exactly as it was.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core import ledgers
from gtm_core.ledgers import LedgerUnreadableError, sum_month_costs, sum_run_costs
from gtm_core.metering import workspace_jsonl_month_total, workspace_jsonl_run_total

_MONTH = ledgers._current_year_month()


def _row(cost, run_id=None, ts=None) -> str:
    rec = {"ts": ts or f"{_MONTH}-05T00:00:00Z", "cost_usd": cost}
    if run_id is not None:
        rec["run_id"] = run_id
    return json.dumps(rec)


def _write(path: Path, *lines: str, trailing_newline: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if trailing_newline else ""), encoding="utf-8")
    return path


# ── month total ──────────────────────────────────────────────────────────────


def test_default_read_still_skips_a_corrupt_interior_line(tmp_path):
    p = _write(tmp_path / "costs.jsonl", _row(1.0), "{not json", _row(2.0))
    assert sum_month_costs(p) == 3.0


def test_strict_read_refuses_a_corrupt_interior_line(tmp_path):
    p = _write(tmp_path / "costs.jsonl", _row(1.0), "{not json", _row(2.0))
    with pytest.raises(LedgerUnreadableError):
        sum_month_costs(p, strict=True)


def test_strict_read_tolerates_a_torn_final_line(tmp_path):
    """A crash mid-append leaves a partial last line; that alone must not make a tenant unbillable."""
    p = _write(
        tmp_path / "costs.jsonl", _row(1.0), _row(2.0), '{"ts": "2026-1', trailing_newline=False
    )
    assert sum_month_costs(p, strict=True) == 3.0


def test_strict_read_refuses_a_row_whose_cost_is_not_a_number(tmp_path):
    p = _write(tmp_path / "costs.jsonl", _row(1.0), _row("lots"), _row(2.0))
    assert sum_month_costs(p) == 3.0  # default: skipped
    with pytest.raises(LedgerUnreadableError):
        sum_month_costs(p, strict=True)


def test_strict_read_refuses_an_unreadable_file(tmp_path, monkeypatch):
    p = _write(tmp_path / "costs.jsonl", _row(1.0))
    real_open = Path.open

    def deny(self, *a, **k):
        if self == p:
            raise PermissionError("denied")
        return real_open(self, *a, **k)

    monkeypatch.setattr(Path, "open", deny)
    with pytest.raises(LedgerUnreadableError):
        sum_month_costs(p, strict=True)


def test_a_ledger_that_does_not_exist_is_zero_even_when_strict(tmp_path):
    """No file means no worker ever metered here — that is knowledge, not an unreadable ledger."""
    assert sum_month_costs(tmp_path / "nope.jsonl", strict=True) == 0.0


# ── run total ────────────────────────────────────────────────────────────────


def test_a_run_total_counts_only_that_runs_rows(tmp_path):
    p = _write(
        tmp_path / "costs.jsonl",
        _row(0.3, "run-a"),
        _row(0.7, "run-b"),
        _row(0.2, "run-a"),
        _row(9.0),  # no run: a VPS-style row, never attributable
    )
    assert sum_run_costs(p, "run-a") == pytest.approx(0.5)
    assert sum_run_costs(p, "run-b") == pytest.approx(0.7)
    assert sum_run_costs(p, "run-c") == 0.0


def test_a_run_total_is_not_limited_to_the_current_month(tmp_path):
    """A run that straddled midnight on the 1st still owes all of its spend."""
    p = _write(tmp_path / "costs.jsonl", _row(1.0, "r", ts="2019-01-31T23:59:00Z"), _row(1.0, "r"))
    assert sum_run_costs(p, "r") == 2.0


def test_a_blank_run_id_matches_nothing(tmp_path):
    """'' must never mean "every row that has no run" — that would bill one run for all of them."""
    p = _write(tmp_path / "costs.jsonl", _row(1.0), _row(2.0, ""))
    assert sum_run_costs(p, "") == 0.0
    assert sum_run_costs(p, None) == 0.0  # type: ignore[arg-type]


def test_a_strict_run_total_refuses_a_corrupt_interior_line(tmp_path):
    p = _write(tmp_path / "costs.jsonl", _row(1.0, "r"), "{bad", _row(2.0, "r"))
    with pytest.raises(LedgerUnreadableError):
        sum_run_costs(p, "r", strict=True)


# ── the workspace roll-ups ───────────────────────────────────────────────────


@pytest.fixture
def ws_root(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    root = tmp_path / "workspaces" / "ws-1" / "content"
    root.mkdir(parents=True)
    return root


def test_the_workspace_run_total_sums_every_profile_ledger(ws_root):
    _write(ws_root / "acme" / "costs.jsonl", _row(0.3, "r1"), _row(5.0, "r2"))
    _write(ws_root / "beta" / "costs.jsonl", _row(0.2, "r1"))
    assert workspace_jsonl_run_total("ws-1", "r1") == pytest.approx(0.5)


def test_the_workspace_run_total_of_a_workspace_with_no_tree_is_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    assert workspace_jsonl_run_total("ws-none", "r1") == 0.0


def test_the_workspace_run_total_is_strict_by_default(ws_root):
    """Settlement cannot guess what a run owed: an unreadable ledger is an error, not zero."""
    _write(ws_root / "acme" / "costs.jsonl", _row(1.0, "r1"), "{bad", _row(1.0, "r1"))
    with pytest.raises(LedgerUnreadableError):
        workspace_jsonl_run_total("ws-1", "r1")


def test_the_workspace_month_total_stays_lenient_unless_asked(ws_root):
    _write(ws_root / "acme" / "costs.jsonl", _row(1.0), "{bad", _row(2.0))
    assert workspace_jsonl_month_total("ws-1") == 3.0
    with pytest.raises(LedgerUnreadableError):
        workspace_jsonl_month_total("ws-1", strict=True)


def test_the_lenient_month_total_still_swallows_an_unreadable_tree(ws_root, monkeypatch):
    """The default read keeps its legacy 'best effort, 0.0' contract; strict is the opt-in."""
    _write(ws_root / "acme" / "costs.jsonl", _row(1.0))

    def boom(*_a, **_k):
        raise PermissionError("denied")

    monkeypatch.setattr(ledgers, "sum_month_costs", boom)
    assert workspace_jsonl_month_total("ws-1") == 0.0
    with pytest.raises(LedgerUnreadableError):
        workspace_jsonl_month_total("ws-1", strict=True)


# ── a crash mid-append must not poison the NEXT append ───────────────────────


def test_an_append_after_a_torn_tail_neither_merges_into_it_nor_leaves_it_behind(tmp_path):
    """A worker killed mid-write leaves a fragment with no newline. The next append used to land on the
    SAME line, fusing the fragment and the new row into one corrupt line: a lenient read then lost the
    new spend, and a strict read raised for ever (an interior corrupt line, not a tolerated tail).
    The appender now cuts the unacknowledged fragment — it was never a record — before it writes."""
    from types import SimpleNamespace

    from gtm_core.ledger_reads import sum_month_costs as strict_sum
    from gtm_core.ledgers import Ledgers

    ledgers = Ledgers(SimpleNamespace(content_root=tmp_path), "acme")
    ledgers.append_cost({"tool": "w", "cost_usd": 1.0})
    path = tmp_path / "acme" / "costs.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"ts": "2026-1')  # the crash: no newline, not a record

    ledgers.append_cost({"tool": "w", "cost_usd": 2.0})

    assert path.read_text(encoding="utf-8").count("\n") == 2  # two whole records, nothing else
    assert strict_sum(path, strict=True) == 3.0  # and a strict read is happy with it
    assert sum_month_costs(path) == 3.0  # the new spend was NOT swallowed

    from gtm_core.ledger_verify import (
        verify_chain,  # the fragment was never chained: it still verifies
    )

    assert verify_chain(path).ok


def test_a_complete_final_row_with_no_newline_is_kept_not_cut(tmp_path):
    """The cut exists for a write that never finished. A WHOLE row that merely lacks its newline (a hand-written
    append, a tool that forgot ``\\n``) is a record somebody was told succeeded: erasing it from an append-only
    audit ledger loses spend silently. It is terminated, kept, and the next append lands on its own line."""
    from types import SimpleNamespace

    from gtm_core.ledgers import Ledgers

    ledgers = Ledgers(SimpleNamespace(content_root=tmp_path), "acme")
    ledgers.append_cost({"tool": "w", "cost_usd": 1.0})
    path = tmp_path / "acme" / "costs.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(
            '{"ts": "2026-10-01T00:00:00+00:00", "tool": "w", "cost_usd": 5.0}'
        )  # whole, no newline

    ledgers.append_cost({"tool": "w", "cost_usd": 2.0})

    assert path.read_text(encoding="utf-8").count("\n") == 3  # three whole records
    assert sum_month_costs(path) == 8.0  # the 5.0 survived


def test_an_append_to_a_clean_or_empty_ledger_is_untouched(tmp_path):
    from types import SimpleNamespace

    from gtm_core.ledgers import Ledgers

    ledgers = Ledgers(SimpleNamespace(content_root=tmp_path), "acme")
    ledgers.append_cost({"tool": "w", "cost_usd": 1.0})
    ledgers.append_cost({"tool": "w", "cost_usd": 2.0})
    assert sum_month_costs(tmp_path / "acme" / "costs.jsonl", strict=True) == 3.0


# ── unit totals: both record shapes, and a row that is not a record we understand ─────


def test_a_unit_total_reads_both_record_shapes_and_only_for_the_named_tool_and_unit(tmp_path):
    from gtm_core.ledger_reads import unit_total_in

    p = tmp_path / "costs.jsonl"
    rows = [
        {
            "ts": f"{_MONTH}-02T00:00:00Z",
            "tool": "apollo",
            "units": {"credits": 3},
        },  # the dict shape
        {
            "ts": f"{_MONTH}-02T00:00:00Z",
            "tool": "apollo",
            "units": 4,
            "unit_kind": "credits",
        },  # scalar
        {
            "ts": f"{_MONTH}-02T00:00:00Z",
            "tool": "apollo",
            "units": 9,
            "unit_kind": "lookups",
        },  # other unit
        {
            "ts": f"{_MONTH}-02T00:00:00Z",
            "tool": "rocketreach",
            "units": {"credits": 50},
        },  # other tool
        {"ts": "2019-01-02T00:00:00Z", "tool": "apollo", "units": {"credits": 70}},  # other month
    ]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert unit_total_in(p, "apollo", "credits") == 7.0


def test_a_row_whose_timestamp_is_not_text_is_skipped_not_a_crash(tmp_path):
    from gtm_core.ledger_reads import unit_total_in

    p = tmp_path / "costs.jsonl"
    rows = [
        {"ts": 12345, "tool": "apollo", "units": {"credits": 5}, "cost_usd": 1.0},
        json.loads(_row(2.0)),
    ]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert sum_month_costs(p) == 2.0
    assert unit_total_in(p, "apollo", "credits") == 0.0


def test_a_unit_count_that_is_not_a_number_refuses_when_strict_and_is_skipped_otherwise(tmp_path):
    from gtm_core.ledger_reads import unit_total_in

    p = tmp_path / "costs.jsonl"
    row = {"ts": f"{_MONTH}-02T00:00:00Z", "tool": "apollo", "units": {"credits": "lots"}}
    p.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert unit_total_in(p, "apollo", "credits") == 0.0
    with pytest.raises(LedgerUnreadableError):
        unit_total_in(p, "apollo", "credits", strict=True)
