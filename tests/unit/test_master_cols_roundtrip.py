from __future__ import annotations

from pathlib import Path

from gtm_core.prospects_consolidate.columns import MASTER_COLS
from gtm_core.prospects_consolidate.io import _atomic_write_csv_cols, _load_master

NEW_COLUMNS = (
    "contact_level",
    "premise_evidence",
    "firmo_source",
    "firmo_on",
    "researched_for_wave",
)


def test_new_columns_in_master_cols():
    """R7.8: Every new pool column must be declared in MASTER_COLS."""
    for col in NEW_COLUMNS:
        assert col in MASTER_COLS, f"{col} missing from MASTER_COLS"


def test_new_columns_roundtrip(tmp_path: Path):
    """R7.8: A master pool CSV carrying values for the new columns round-trips without loss."""
    csv_file = tmp_path / "master-test.csv"

    row_in = {col: f"val_{col}" for col in MASTER_COLS}
    row_in["email"] = "valid@example.com"
    _atomic_write_csv_cols(csv_file, [row_in], MASTER_COLS)

    rows_out = _load_master(csv_file)
    assert len(rows_out) == 1
    row_out = rows_out[0]

    for col in NEW_COLUMNS:
        assert row_out[col] == f"val_{col}", (
            f"Mismatch on {col}: expected 'val_{col}', got '{row_out[col]}'"
        )
