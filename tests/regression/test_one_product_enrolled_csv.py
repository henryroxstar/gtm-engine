"""CM6: the sequencer's input is untouched by the run scope.

``send_cards`` writes the ``<sequence>-enrolled.csv`` a sequencer imports, and registers the sequence
in ``cells.toml``. The one-product-per-run work touches neither, and a product column added to either
would change what the sequencer receives. The expected headers below were captured by running the
pre-change commit (a ``git archive`` of ``efa7e349``, never a worktree) on this same input, and are
typed out here so a change to them has to be made on purpose (§R18: a test that derived its
expectation from the code could not fail).

The registered keys stand in for the plan's "merge-label set": the columns a reply is joined back on.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from gtm_core import send_cards

BASE_HEADER = [
    "email", "Email", "first", "First Name", "last", "Last Name", "company", "Company",
    "cell_id", "segment", "seat", "title",
]  # fmt: skip
BASE_PERSONALISED_HEADER = [*BASE_HEADER, "why_now", "Why Now"]
BASE_CELLS_TOML_KEYS = ["csv", "id", "lane", "spec", "title"]


def _cell(cell_id: str, personalised: bool, sequence: str) -> dict:
    member = {
        "name": "Ada Example",
        "email": "ada@alpha-fictional.example",
        "company": "Fictional Alpha Co",
        "seat": "technical",
        "opener": "shipped a relay",
        "capture_date": "2026-09-01",
        "signal_kind": "event",
        "ticked": True,
    }
    return {
        "cell_id": cell_id,
        "cohort": "enterprise",
        "seat": "technical",
        "message_variant": "v1",
        "angle": "Alpha angle",
        "segment": "enterprise",
        "premise_ids": [],
        "proof_ids": [],
        "gate_receipt": {
            "integrity": "pass",
            "suppression": "pass",
            "compliance": "pass",
            "freshness": "pass",
        },
        "is_personalised": personalised,
        "example_member": {
            "name": member["name"],
            "email": member["email"],
            "company": member["company"],
            "subject": "s",
            "body": "<p>b</p>",
        },
        "members": [member],
        "panel_verdicts": [],
        "sequence_id": sequence,
        "step_id": f"step-{sequence}",
        "title": "Head of Platform",
    }


def test_the_enrolled_csv_columns_and_registered_keys_match_the_pre_change_capture(tmp_path: Path):
    cells = [_cell("c:pers", True, "seq-pers"), _cell("c:gen", False, "seq-gen")]
    export = send_cards.create_card_export(
        cells,
        decisions={"c:pers": "send this cell", "c:gen": "send this cell"},
        revealed_before={"c:pers": True, "c:gen": True},
        run_id="run-1",
    )
    path = tmp_path / "export.json"
    path.write_text(json.dumps(export), encoding="utf-8")

    send_cards.send_cards_apply(path, profile="demo", content_root=tmp_path, run_id="run-1")

    seq = tmp_path / "demo" / "prospects" / "sequences"

    def header(name: str) -> list[str]:
        with (seq / name).open(newline="", encoding="utf-8") as fh:
            return next(csv.reader(fh))

    assert header("seq-gen-enrolled.csv") == BASE_HEADER
    assert header("seq-pers-enrolled.csv") == BASE_PERSONALISED_HEADER
    keys = {
        line.split("=")[0].strip()
        for line in (seq / "cells.toml").read_text(encoding="utf-8").splitlines()
        if "=" in line
    }
    assert sorted(keys) == BASE_CELLS_TOML_KEYS
    assert not {"product", "product_slug"} & keys
