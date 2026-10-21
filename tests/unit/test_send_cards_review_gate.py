"""``send_cards apply`` refuses a "send this cell" that never claims it was opened on the page.

The incident (2026-09-26): an agent called ``create_card_export(decisions={all cells: "send this
cell"})`` and applied it. The export recorded ``revealed_before_decision: false`` and nobody ticked
or unticked a member. Apply wrote three drafts, three enrolled lists and three placeholder
registrations in ``cells.toml``. Fictional data only (§R9).
"""

from __future__ import annotations

import json

import pytest

from gtm_core import send_cards
from gtm_core.send_cards_review_gate import REFUSAL, refuse_unreviewed

A, B, C = (
    "base:enterprise:security:cell-a",
    "base:enterprise:technical:cell-b",
    "base:startup:ceo:cell-c",
)


def cell(cell_id: str, n: int) -> dict:
    return {
        "cell_id": cell_id,
        "cohort": "enterprise",
        "seat": cell_id.split(":")[2],
        "angle": "threat-intel",
        "segment": cell_id.split(":")[1],
        "title": f"Fictional cell {n}",
        "sequence_id": f"seq-{n:02d}",
        "step_id": f"step-{n:02d}",
        "spec": f"spec-{n}.md",
        "is_personalised": False,
        "members": [
            {
                "email": f"person{n}@corp{n}.example",
                "first": "Sam",
                "last": "Lee",
                "company": f"Corp{n}",
            }
        ],
    }


CELLS = [cell(A, 1), cell(B, 2), cell(C, 3)]
SEND = "send this cell"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))

    class Home:
        root = tmp_path
        seq = tmp_path / "demo" / "prospects" / "sequences"
        export = tmp_path / "export.json"

        def write(self, **kw):
            data = send_cards.create_card_export(CELLS, run_id="run-1", **kw)
            self.export.write_text(json.dumps(data), encoding="utf-8")
            return data

        def apply(self):
            return send_cards.send_cards_apply(self.export, profile="demo", content_root=tmp_path)

        def written(self) -> list[str]:
            return sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file())

    return Home()


def test_the_incident_export_is_refused_in_full_and_writes_nothing(home):
    """Every cell 'sent', nothing claimed opened: no draft, no enrolled list, no registration."""
    home.write(decisions={A: SEND, B: SEND, C: SEND})
    before = home.written()
    result = home.apply()
    assert result.drafts == [] and result.draft_paths == []
    assert [r["cell_id"] for r in result.refused_cells] == [A, B, C]
    assert not (home.seq / "cells.toml").exists()  # the placeholder registrations never happen
    assert not list(home.seq.glob("*-enrolled.csv"))
    assert home.written() == before == [home.export.name]


def test_the_refusal_is_in_plain_words_and_names_the_cell(home):
    home.write(decisions={A: SEND})
    (refusal,) = home.apply().refused_cells
    assert refusal["reason"] == (
        f"Cell {A} was approved without being opened on the review page. "
        "Open the page, reveal the emails, then decide."
    )
    assert refusal["reason"] == REFUSAL.format(cell_id=A) and refusal["decision"] == SEND


def test_the_refusal_is_per_cell_so_a_valid_cell_still_applies(home):
    home.write(decisions={A: SEND, B: SEND, C: SEND}, revealed_before={B: True})
    result = home.apply()
    assert [d["card_ids"] for d in result.drafts] == [[B]]
    assert [r["cell_id"] for r in result.refused_cells] == [A, C]
    # The survivor is the only approved cell, so it keeps the single-draft file name.
    assert [p.name for p in result.draft_paths] == ["run-1.enroll-draft.json"]
    assert (home.seq / "seq-02-enrolled.csv").is_file()
    assert not (home.seq / "seq-01-enrolled.csv").exists()


def test_a_valid_export_still_applies_exactly_as_before(home):
    home.write(decisions={A: SEND, B: SEND}, revealed_before={A: True, B: True})
    result = home.apply()
    assert len(result.drafts) == 2 and result.refused_cells == []
    assert sorted(p.name for p in result.draft_paths) == [
        f"run-1-{A}.enroll-draft.json",
        f"run-1-{B}.enroll-draft.json",
    ]


def test_a_missing_key_refuses_as_surely_as_false(home):
    data = home.write(decisions={A: SEND}, revealed_before={A: True})
    del data["cards"][0]["revealed_before_decision"]
    home.export.write_text(json.dumps(data), encoding="utf-8")
    assert [r["cell_id"] for r in home.apply().refused_cells] == [A]


@pytest.mark.parametrize("claim", [False, None, "true", "yes", 1, 0, "", [True]])
def test_only_the_literal_true_grants(home, claim):
    data = home.write(decisions={A: SEND}, revealed_before={A: True})
    data["cards"][0]["revealed_before_decision"] = claim
    home.export.write_text(json.dumps(data), encoding="utf-8")
    assert [r["cell_id"] for r in home.apply().refused_cells] == [A]


def test_decisions_that_send_nothing_need_no_claim(home):
    home.write(decisions={A: "not this wave", B: "rewrite", C: ""}, notes={B: "re-angle it"})
    result = home.apply()
    assert result.refused_cells == [] and result.drafts == []
    assert [r["cell_id"] for r in result.repair_rows] == [B]


def test_an_unknown_decision_word_is_still_refused_as_before(home):
    home.write(decisions={A: SEND, B: "approve"})
    with pytest.raises(ValueError, match="unknown decision 'approve'"):
        home.apply()


def test_the_filter_alone_leaves_other_cards_untouched():
    cards = [{"cell_id": "x", "decision": "rewrite"}, {"cell_id": "y", "decision": SEND}]
    kept, refused = refuse_unreviewed(cards)
    assert kept == [cards[0]] and [r["cell_id"] for r in refused] == ["y"]
    assert (
        refuse_unreviewed([{"cell_id": "z", "decision": SEND, "revealed_before_decision": True}])[1]
        == []
    )


# ── the command line ──────────────────────────────────────────────────────────────────────────


def test_the_cli_lists_each_refusal_and_exits_2(home, capsys):
    home.write(decisions={A: SEND, B: SEND}, revealed_before={B: True})
    code = send_cards.main(["apply", "--export", str(home.export), "--profile", "demo"])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out.strip() == (
        "outreach-campaign apply: wrote 1 draft(s), 0 repair row(s), "
        "REFUSED 1 cell(s) that were not opened on the page"
    )
    assert captured.err.strip() == f"REFUSED: {REFUSAL.format(cell_id=A)}"


def test_the_cli_summary_for_a_clean_export_is_unchanged(home, capsys):
    home.write(decisions={A: SEND}, revealed_before={A: True})
    code = send_cards.main(["apply", "--export", str(home.export), "--profile", "demo"])
    assert code == 0
    assert (
        capsys.readouterr().out.strip()
        == "outreach-campaign apply: wrote 1 draft(s), 0 repair row(s)"
    )
