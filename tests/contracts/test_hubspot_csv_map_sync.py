"""Contract: the export column map cannot omit a column the gate blocks on.

Hand-maintained, `hubspot-csv-map.md` was the only documented schema for the CSV that
carries a run from `prospect` to `email-sequence` — and it listed none of the nine
record and verdict columns the enrollment gate refuses a list for. A run following it
exactly produced a list the gate rejected, and the document looked complete while doing
it. Worse, the columns had no aliases either, so the record could not be imported from
an export at all.

Same mechanism as the skill codegen: generated from the code, and held in sync by a
test that fails on drift rather than by anyone remembering.
"""

from __future__ import annotations

from gtm_core import schema_doc
from gtm_core.prospects_consolidate import _ALIASES, MASTER_COLS, csv_map_markdown
from gtm_core.signal_record import JUDGE_COLUMNS, RECORD_COLUMNS, SIGNAL_COLUMN


def test_the_committed_map_is_in_sync():
    assert schema_doc.check(), (
        "hubspot-csv-map.md is stale — regenerate it with "
        "`uv run python -m gtm_core.prospects schema-doc`"
    )


def test_every_master_column_is_accounted_for():
    """Either a run writes it, or the pipeline assigns it. Nothing is simply absent."""
    rendered = csv_map_markdown()
    missing = []
    for col in MASTER_COLS:
        spellings = _ALIASES.get(col, (col,))
        if not any(f"`{s}`" in rendered for s in spellings):
            missing.append(col)
    assert not missing, f"columns exist in MASTER_COLS but appear nowhere in the map: {missing}"


def test_the_record_columns_are_documented():
    """The exact omission that broke the handoff."""
    rendered = csv_map_markdown()
    for col in (*RECORD_COLUMNS, SIGNAL_COLUMN):
        primary = _ALIASES.get(col, (col,))[0]
        assert f"`{primary}`" in rendered, f"{col} is gate-critical and undocumented"


def test_the_record_columns_are_importable():
    """Documenting them is not enough — `_get` returns '' for an unaliased field, so a
    column with no alias cannot enter the pipeline from an export however well it is
    described."""
    for col in (*RECORD_COLUMNS, SIGNAL_COLUMN):
        assert col in _ALIASES, f"{col} is documented but has no import alias"


def test_machine_assigned_columns_are_not_offered_as_inputs():
    """An export that could set `judge_verdict` would be an input claiming to be judged."""
    rendered = csv_map_markdown()
    assigned = rendered.split("## Columns the pipeline assigns", 1)[1]
    for col in (*JUDGE_COLUMNS, "pool_row_id", "account_id", "suppression"):
        assert f"`{col}`" in assigned, f"{col} must be listed as pipeline-assigned"


def test_the_generated_file_warns_against_hand_editing():
    assert "GENERATED" in csv_map_markdown()


def test_regeneration_is_byte_stable(tmp_path):
    out = tmp_path / "map.md"
    schema_doc.generate(out)
    once = out.read_bytes()
    schema_doc.generate(out)
    assert out.read_bytes() == once


def test_check_fails_on_a_stale_file(tmp_path):
    """Positive control: the sync check must be capable of failing."""
    stale = tmp_path / "map.md"
    stale.write_text("# not the schema\n", encoding="utf-8")
    assert not schema_doc.check(stale)
