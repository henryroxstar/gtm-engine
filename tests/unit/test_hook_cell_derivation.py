"""Tests for deterministic hook_cell derivation.

Fictional fixtures only (§R9).
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from gtm_core.hook_cell import derive_hook_cell, parse_hook_cell
from gtm_core.hook_coverage.config import parse_matrix
from gtm_core.lanes.context import RouterContext
from gtm_core.lanes.model import HOLD_ORDER, PROTECTIVE_HOLD_TRIGGERS
from gtm_core.lanes.router import route_row
from gtm_core.prospects_consolidate.columns import MASTER_COLS
from gtm_core.prospects_consolidate.io import _atomic_write_csv_cols, _load_master

SAMPLE_MATRIX = """---
source: manual
---
# Hook matrix — Test Company

## Startup

| Signal → / Persona ↓ | security | funding | generic |
|---|---|---|---|
| **CEO** | "Security opener" | "Funding opener" | "Generic startup opener" |

## Enterprise

| Signal → / Persona ↓ | security | compliance |
|---|---|---|
| **CISO** | "Enterprise security" | "Enterprise compliance" |
"""


@pytest.fixture
def sample_matrix():
    return parse_matrix(SAMPLE_MATRIX)


# ---------------------------------------------------------------------------
# Positive Controls (Happy Path)
# ---------------------------------------------------------------------------


def test_matrix_lookup_success(sample_matrix):
    """Valid (segment, signal) returns '{segment}|{signal}'."""
    coord, err = derive_hook_cell("startup", "security", matrix=sample_matrix)
    assert err == ""
    assert coord == "startup|security"


def test_graceful_generic_fallback(sample_matrix):
    """An unmapped signal falls back to '{segment}|generic' if generic column exists."""
    coord, err = derive_hook_cell("startup", "new-office", matrix=sample_matrix)
    assert err == ""
    assert coord == "startup|generic"


# ---------------------------------------------------------------------------
# Degradation & Refusals (Sad Path)
# ---------------------------------------------------------------------------


def test_missing_signal_and_no_generic(sample_matrix):
    """When neither signal nor generic column exists, returns actionable hold detail."""
    coord, err = derive_hook_cell("enterprise", "new-office", matrix=sample_matrix)
    assert coord == ""
    assert err == "Add 'new-office' or 'generic' column to enterprise row in hook-matrix.md"


def test_missing_segment(sample_matrix):
    """If segment is empty, lookup fails and returns actionable refusal."""
    coord, err = derive_hook_cell("", "security", matrix=sample_matrix)
    assert coord == ""
    assert err == "Add 'security' or 'generic' column to  row in hook-matrix.md"


def test_corrupted_hook_cell_fails_loudly():
    """Corrupted ledger input fails loudly via parse_hook_cell."""
    with pytest.raises(ValueError, match="malformed hook_cell coordinate"):
        parse_hook_cell("invalid:format")

    with pytest.raises(ValueError, match="malformed hook_cell coordinate"):
        parse_hook_cell("no_pipe")

    with pytest.raises(ValueError, match="malformed hook_cell coordinate"):
        parse_hook_cell("startup|")


# ---------------------------------------------------------------------------
# Ledger Schema & Backward Compatibility
# ---------------------------------------------------------------------------


def test_ledger_schema_persistence_roundtrip(tmp_path: Path):
    """hook_cell column persists and roundtrips in prospects_consolidate."""
    assert "hook_cell" in MASTER_COLS

    csv_path = tmp_path / "master-list.csv"
    row = {col: f"val_{col}" for col in MASTER_COLS}
    row["email"] = "test@example.com"
    row["hook_cell"] = "startup|security"

    _atomic_write_csv_cols(csv_path, [row], MASTER_COLS)
    loaded = _load_master(csv_path)

    assert len(loaded) == 1
    assert loaded[0]["hook_cell"] == "startup|security"


def test_backward_compatibility_missing_column(tmp_path: Path):
    """Legacy CSV lacking hook_cell populates '' and routes to missing-hook-cell hold."""
    legacy_cols = [c for c in MASTER_COLS if c != "hook_cell"]
    csv_path = tmp_path / "legacy.csv"

    legacy_row = dict.fromkeys(legacy_cols, "")
    legacy_row["email"] = "legacy@example.com"
    legacy_row["segment"] = "enterprise"
    legacy_row["signal_column"] = "compliance"
    legacy_row["verdict"] = "send"
    legacy_row["why_now"] = "Recent compliance change"
    legacy_row["signal_observed"] = "2026-09-01"

    # Write legacy CSV without hook_cell column header
    _atomic_write_csv_cols(csv_path, [legacy_row], legacy_cols)

    loaded = _load_master(csv_path)
    assert len(loaded) == 1
    assert loaded[0]["hook_cell"] == ""

    # Route row through router
    ctx = RouterContext(profile="test", as_of=datetime.date(2026, 9, 3))
    routed = route_row(loaded[0], ctx, None)

    assert routed.lane == "hold"
    assert routed.trigger == "missing-hook-cell"
    assert (
        routed.detail == "Add 'compliance' or 'generic' column to enterprise row in hook-matrix.md"
    )


def test_protective_hold_properties():
    """missing-hook-cell must be in HOLD_ORDER and PROTECTIVE_HOLD_TRIGGERS."""
    assert "missing-hook-cell" in HOLD_ORDER
    assert "missing-hook-cell" in PROTECTIVE_HOLD_TRIGGERS
