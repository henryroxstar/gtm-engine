"""`ingest --source` must gate BILLING, not just stamp a label.

The defect this pins: `ingest()` used to call `log_vibe_export_cost()` unconditionally.
Pointed at a curated spreadsheet — a list nobody bought per row — that wrote
`rows x EXPORT_CREDITS_PER_ROW` into `costs.jsonl`: ~$16 of fabricated spend on a
400-row sheet, charged against the profile's §R2 monthly cap, which then blocks real
paid calls. List mode (discovery-and-budget.md §"List mode") depends on this gate.

The negative control is the point: if BOTH branches billed, or NEITHER did, the flag
would look like it worked while doing nothing. `test_metered_and_unmetered_differ`
fails in both of those worlds.

Fictional companies only (§R9).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core.prospects_import import ingest

PROFILE = "testtenant"


def _curated_sheet(path: Path, rows: int = 10) -> Path:
    """A hand-curated sheet: plain human headers, none of Vibe's `business_*` names."""
    lines = ["Company,Website,Country,Industry,Employees"]
    lines += [
        f"Nimbus Freight {i} Pte Ltd,nimbusfreight{i}.example,Singapore,Logistics,51-200"
        for i in range(rows)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _cost_rows(root: Path) -> list[dict]:
    ledger = root / PROFILE / "costs.jsonl"
    if not ledger.exists():
        return []
    return [json.loads(x) for x in ledger.read_text().splitlines() if x.strip()]


def _candidates(root: Path, run: str) -> list[dict]:
    p = root / PROFILE / "prospects" / "imports" / f"candidates-{run}.json"
    return json.loads(p.read_text())["candidates"]


@pytest.fixture
def sheet(tmp_path):
    return _curated_sheet(tmp_path / "curated.csv")


def test_curated_source_writes_no_cost(tmp_path, sheet):
    out = ingest(sheet, PROFILE, "run-curated", content_root=tmp_path, source="curated-sheet")
    assert out["cost_usd"] == 0.0
    assert out["billed_rows"] == 0
    assert _cost_rows(tmp_path) == [], "an unbought list must not touch the cost ledger"


def test_default_source_still_bills_exactly_as_before(tmp_path, sheet):
    """The metered path is unchanged — this flag must not quietly stop Vibe billing."""
    out = ingest(sheet, PROFILE, "run-metered", content_root=tmp_path)
    assert out["cost_usd"] > 0
    assert out["billed_rows"] == 10
    assert len(_cost_rows(tmp_path)) == 1


def test_metered_and_unmetered_differ(tmp_path, sheet):
    """The discrimination check. Guards against a flag that is read but never acted on."""
    paid = ingest(sheet, PROFILE, "r-paid", content_root=tmp_path)
    free = ingest(sheet, PROFILE, "r-free", content_root=tmp_path, source="curated-sheet")
    assert paid["cost_usd"] != free["cost_usd"]
    assert len(_cost_rows(tmp_path)) == 1, "exactly one of the two runs may bill"


def test_source_is_stamped_on_every_candidate(tmp_path, sheet):
    """Provenance and billing move together: a row claiming a metered origin was billed."""
    ingest(sheet, PROFILE, "run-curated", content_root=tmp_path, source="curated-sheet")
    ingest(sheet, PROFILE, "run-metered", content_root=tmp_path)
    assert {c["source"] for c in _candidates(tmp_path, "run-curated")} == {"curated-sheet"}
    assert {c["source"] for c in _candidates(tmp_path, "run-metered")} == {"vibe-export"}


def test_human_headers_parse_without_vibe_column_names(tmp_path, sheet):
    """List mode's premise: `ingest` is not Vibe-only. If the alias table is narrowed,
    a curated sheet silently yields zero candidates — this catches that."""
    out = ingest(sheet, PROFILE, "run-curated", content_root=tmp_path, source="curated-sheet")
    assert out["parsed"] == 10, "plain Company/Website/Country headers must be understood"
    first = _candidates(tmp_path, "run-curated")[0]
    assert first["domain"].endswith(".example"), "Website must map to the domain field"
    assert first["market"] == "Singapore", "Country must map to market"


def test_curated_rows_carry_no_heat(tmp_path, sheet):
    """Heat 0 is correct, not a bug: a curated sheet carries no intent topics. Documented
    in §List mode so nobody 'fixes' it by inventing a default."""
    ingest(sheet, PROFILE, "run-curated", content_root=tmp_path, source="curated-sheet")
    assert all(c["heat"] == 0 for c in _candidates(tmp_path, "run-curated"))
