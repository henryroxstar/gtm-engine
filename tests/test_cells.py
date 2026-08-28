"""Tests for gtm_core.cells — the segment x seat x variant learning unit."""

from __future__ import annotations

import json

import pytest

from gtm_core import cells
from gtm_core import prospects_consolidate as pc

CSV_HEADER = "first,last,email,title,company,company_domain,city,country,segment,tier,signal_clause,why_now,case_study,src,suppression,suppression_date\n"


def _row(title, segment, suppression=""):
    return (
        f"Ada,Byte,ada@acme.example,{title},Acme,acme.example,SG,Singapore,"
        f"{segment},A,Acme ships things,,,,{suppression},\n"
    )


def _write_seq(tmp_path, profile, name, rows):
    d = pc._prospects_dir(profile, tmp_path) / "sequences"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(CSV_HEADER + "".join(rows), encoding="utf-8")
    return d


def _write_map(tmp_path, profile, body):
    d = pc._prospects_dir(profile, tmp_path) / "sequences"
    d.mkdir(parents=True, exist_ok=True)
    (d / "cells.toml").write_text(body, encoding="utf-8")


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("spec-run500-enterprise-security-2026-08-18.md", "run500-enterprise-security"),
        ("content/x/spec-run500-startup-2026-08-18.md", "run500-startup"),
        ("spec-wave1-agent-fact-2026-08-11.md", "wave1-agent-fact"),
        ("no-prefix.md", "no-prefix"),
    ],
)
def test_variant_of_strips_prefix_and_trailing_date(spec, expected):
    assert cells.variant_of(spec) == expected


def test_build_cells_splits_by_segment_and_seat(tmp_path):
    profile = "acme"
    _write_seq(
        tmp_path,
        profile,
        "list.csv",
        [
            _row("CISO", "enterprise"),
            _row("Chief Information Security Officer", "enterprise"),
            _row("CTO", "enterprise"),
            # Resolves to the `partnership` PERSONA but to NO seat (0-1 recipients in
            # the whole pool, so no copy is owed) — it must still surface as unknown
            # rather than being folded into someone else's bucket.
            _row("Head of Partnerships", "enterprise"),
        ],
    )
    _write_map(
        tmp_path,
        profile,
        '[[sequence]]\nid = "S1"\ncsv = "list.csv"\nspec = "spec-alpha-2026-08-18.md"\n',
    )

    model = cells.build_cells(profile, tmp_path)
    by_id = {c["cell_id"]: c for c in model["cells"]}

    assert by_id["enterprise:security:alpha"]["enrolled"] == 2
    assert by_id["enterprise:cto:alpha"]["enrolled"] == 1
    # A title with no seat -- unrecognised, or a persona that is deliberately seatless --
    # is reported as unknown, never silently folded into a seat.
    assert by_id["enterprise:unknown:alpha"]["enrolled"] == 1


def test_suppression_column_reduces_sendable(tmp_path):
    profile = "acme"
    _write_seq(
        tmp_path,
        profile,
        "list.csv",
        [_row("CISO", "enterprise"), _row("CISO", "enterprise", suppression="opted-out")],
    )
    _write_map(
        tmp_path,
        profile,
        '[[sequence]]\nid = "S1"\ncsv = "list.csv"\nspec = "spec-alpha-2026-08-18.md"\n',
    )
    cell = cells.build_cells(profile, tmp_path)["cells"][0]
    assert (cell["enrolled"], cell["suppressed"], cell["sendable"]) == (2, 1, 1)


def test_outcome_rows_join_by_cell_tag(tmp_path):
    profile = "acme"
    _write_seq(tmp_path, profile, "list.csv", [_row("CISO", "enterprise")] * 10)
    _write_map(
        tmp_path,
        profile,
        '[[sequence]]\nid = "S1"\ncsv = "list.csv"\nspec = "spec-alpha-2026-08-18.md"\n',
    )
    cid = "enterprise:security:alpha"
    rows = [
        {"outcome": "sent", "value": 10, "tags": [f"cell:{cid}"]},
        {"outcome": "reply", "value": 2, "tags": [f"cell:{cid}"]},
        {"outcome": "positive_reply", "value": 1, "tags": [f"cell:{cid}"]},
        # A row for a cell that does not exist must be ignored, not invented.
        {"outcome": "reply", "value": 99, "tags": ["cell:ghost:ghost:ghost"]},
    ]
    cell = cells.build_cells(profile, tmp_path, outcome_rows=rows)["cells"][0]
    assert cell["sent"] == 10
    # positive_reply is in REPLY_OUTCOMES, so it counts once as a reply and once as positive.
    assert cell["replied"] == 3
    assert cell["positive"] == 1
    assert cell["reply_rate"] == 0.3
    lo, hi = cell["ci95"]
    assert lo < 0.3 < hi


def test_missing_cell_map_yields_nothing_rather_than_guessing(tmp_path):
    """Fail-closed: inventing the sequence->list join would mis-attribute replies."""
    profile = "acme"
    _write_seq(tmp_path, profile, "list.csv", [_row("CISO", "enterprise")])
    assert cells.build_cells(profile, tmp_path)["cells"] == []


def test_comparability_requires_exactly_one_differing_dimension(tmp_path):
    """One shared body across three seats is comparable on seat; a seat split that
    also changes the copy is comparable on nothing."""
    profile = "acme"
    _write_seq(tmp_path, profile, "shared.csv", [_row("CISO", "startup"), _row("CTO", "startup")])
    _write_seq(tmp_path, profile, "sec.csv", [_row("CISO", "enterprise")])
    _write_seq(tmp_path, profile, "tech.csv", [_row("CTO", "enterprise")])
    _write_map(
        tmp_path,
        profile,
        '[[sequence]]\nid = "A"\ncsv = "shared.csv"\nspec = "spec-shared-2026-08-18.md"\n'
        '[[sequence]]\nid = "B"\ncsv = "sec.csv"\nspec = "spec-sec-2026-08-18.md"\n'
        '[[sequence]]\nid = "C"\ncsv = "tech.csv"\nspec = "spec-tech-2026-08-18.md"\n',
    )
    by_id = {c["cell_id"]: c for c in cells.build_cells(profile, tmp_path)["cells"]}

    # Same variant, different seat -> seat is cleanly readable.
    assert any(
        "seat vs startup:cto:shared" in e for e in by_id["startup:security:shared"]["comparable_on"]
    )
    # Seat AND variant both move -> nothing is readable between them.
    assert by_id["enterprise:security:sec"]["comparable_on"] == []


def test_wilson_and_detectable_lift_edges():
    assert cells.wilson(0, 0) is None  # no denominator -> unknown, never 0%
    lo, hi = cells.wilson(1, 10)
    assert 0 <= lo < 0.1 < hi <= 1
    # More observations must never widen the detectable effect.
    assert cells.detectable_lift(1000, 0.059) < cells.detectable_lift(100, 0.059)
    assert cells.detectable_lift(0, 0.059) is None


def test_cli_text_and_json(tmp_path, capsys, monkeypatch):
    profile = "acme"
    _write_seq(tmp_path, profile, "list.csv", [_row("CISO", "enterprise")])
    _write_map(
        tmp_path,
        profile,
        '[[sequence]]\nid = "S1"\ncsv = "list.csv"\nspec = "spec-alpha-2026-08-18.md"\n',
    )
    assert cells._cli(["--profile", profile, "--content-root", str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["cells"][0]["cell_id"] == "enterprise:security:alpha"

    assert (
        cells._cli(["--profile", profile, "--content-root", str(tmp_path), "--format", "text"]) == 0
    )
    assert "enterprise:security:alpha" in capsys.readouterr().out


def test_intent_profile_reports_coverage_and_absent_feeds(tmp_path):
    """Coverage is the load-bearing number: a score distribution drawn from half the list
    is not a description of the list. And an absent feed must be shown as absent, not
    omitted — otherwise "we hold no hiring signal" is indistinguishable from "hiring
    signal isn't a thing we collect"."""
    profile = "acme"
    _write_seq(
        tmp_path,
        profile,
        "list.csv",
        [_row("CISO", "enterprise"), _row("CTO", "enterprise")],
    )
    # Second row's company is absent from the pool, so it must count as unmatched.
    d = pc._prospects_dir(profile, tmp_path) / "sequences"
    rows = (d / "list.csv").read_text(encoding="utf-8").splitlines(keepends=True)
    rows[2] = rows[2].replace("acme.example", "nowhere.example")
    (d / "list.csv").write_text("".join(rows), encoding="utf-8")

    _write_map(
        tmp_path,
        profile,
        '[[sequence]]\nid = "S1"\ncsv = "list.csv"\nspec = "spec-alpha-2026-08-18.md"\n',
    )
    (pc._prospects_dir(profile, tmp_path) / "latest.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "domain": "acme.example",
                        "score": 40,
                        "intent_feeds": ["vibe-topic"],
                        "intent_topics": [{"topic": "agentic ai", "score": 70}],
                        "qualification_path": "intent-only-relaxed",
                        "new_in_role": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    p = cells.intent_profile(profile, tmp_path)
    assert (p["total"], p["matched"], p["unmatched"]) == (2, 1, 1)
    assert p["score_min"] == p["score_max"] == 40

    by_key = {f["key"]: f for f in p["feeds"]}
    assert by_key["vibe-topic"]["present"] is True
    # Every roster feed is reported, present or not.
    assert set(by_key) == {k for k, *_ in cells.INTENT_FEEDS}
    assert by_key["rr-hiring"]["present"] is False and by_key["rr-hiring"]["n"] == 0

    assert p["topics"][0]["topic"] == "agentic ai"
    assert p["topics"][0]["avg_score"] == 70.0
    assert p["new_in_role"] == {"true": 0, "false": 1, "unknown": 0}


def test_intent_profile_without_a_pool_reports_zero_coverage(tmp_path):
    profile = "acme"
    _write_seq(tmp_path, profile, "list.csv", [_row("CISO", "enterprise")])
    _write_map(
        tmp_path,
        profile,
        '[[sequence]]\nid = "S1"\ncsv = "list.csv"\nspec = "spec-alpha-2026-08-18.md"\n',
    )
    p = cells.intent_profile(profile, tmp_path)
    assert (p["matched"], p["unmatched"], p["score_n"]) == (0, 1, 0)
    assert all(not f["present"] for f in p["feeds"])


def test_supply_profile_separates_suppressed_from_qualified(tmp_path):
    """A row held back by the signal-quality pass is not mailable, however good the job
    title. Goals set on "qualified" alone credit the campaign with people already ruled out."""
    profile = "acme"
    _write_seq(
        tmp_path,
        profile,
        "list.csv",
        [
            _row("CISO", "enterprise"),
            _row("CTO", "enterprise", suppression="signal-off-topic: no agent content"),
            _row("Head of Partnerships", "enterprise"),
        ],
    )
    _write_map(
        tmp_path,
        profile,
        '[[sequence]]\nid = "S1"\ncsv = "list.csv"\nspec = "spec-alpha-2026-08-18.md"\n',
    )
    p = cells.supply_profile(profile, tmp_path)
    assert p["total"] == 3
    assert p["suppressed"] == 1
    assert p["sendable"] == 2
    # CISO and CTO are role-fit, but the CTO is suppressed.
    assert p["qualified"] == 2
    assert p["qualified_sendable"] == 1
    assert p["suppression_reasons"][0] == {"reason": "signal-off-topic", "n": 1}
