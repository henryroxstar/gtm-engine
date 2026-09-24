"""The hook backlog walks the WHOLE matrix and reports every bucket with its number.

What the 2026-09-23 hunt found surviving in `gtm_core/hook_coverage/backlog.py`: the `continue`
past a declared cell mutated to `break` (every fixture declared a cell that sat LAST in matrix
order, so stopping there changed nothing); the `used` arithmetic, `declared_pairs`, both sorts,
the `include_drafts` fold and the whole UNMAPPED PERSONA section — the one path a matrix row
with a persona label the tenant's cue list cannot join is reported through — were never
executed by any test. A drafter acts on this report by writing the arguments it lists, so a
backlog that stops at the first used cell, or drops the unjoinable rows, sends them to write
the wrong ones. Invented fixture (§R9) — same shape as `test_hook_backlog.py`, two segments.
"""

from __future__ import annotations

from pathlib import Path

from gtm_core.hook_coverage.backlog import Backlog, backlog, render_backlog

PROFILE = "acme"

_MATRIX = """\
# Hook matrix

## Startup

| Signal → / Persona ↓ | Tide chart drifted | Kiln log arrived |
|---|---|---|
| **CISO** | A firing schedule nobody signed leaves the glaze on somebody's word. | Answering in the log beats answering from memory. |
| **CTO** | Two tide charts disagree by a full hour at the same berth. | The kiln log is the question already written down. |

## Enterprise

| Signal → / Persona ↓ | Tide chart drifted |
|---|---|
| **Kiln Warden** | A warden's tally and the kiln log drift apart by one firing a week. |
| **Harbour Master** | The berth ledger and the tide chart never agree on the same hour. |
"""


def _spec(cell: str) -> str:
    return f"```\nCampaign: q4\nhook_cell: {cell}\n```\n\nBody.\n"


def _tenant(
    tmp_path: Path,
    *,
    specs: dict[str, str],
    drafts: dict[str, str] | None = None,
    matrix: str = _MATRIX,
):
    knowledge = tmp_path / "profiles" / PROFILE / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "hook-matrix.md").write_text(matrix, encoding="utf-8")
    seq = tmp_path / "content" / PROFILE / "prospects" / "sequences"
    seq.mkdir(parents=True)
    entries = []
    for name, text in specs.items():
        (seq / name).write_text(text, encoding="utf-8")
        csv_name = name.replace(".md", ".csv")
        (seq / csv_name).write_text("email\na@x.example\n", encoding="utf-8")
        entries.append(
            f'[[sequence]]\nid = "{name}"\ncsv = "{csv_name}"\nspec = "{name}"\ncampaign = "q4"\n'
        )
    (seq / "cells.toml").write_text("\n".join(entries), encoding="utf-8")
    for slug, text in (drafts or {}).items():
        cell_dir = tmp_path / "content" / PROFILE / "prospects" / "evals" / "drafts" / slug
        cell_dir.mkdir(parents=True)
        (cell_dir / "spec.md").write_text(text, encoding="utf-8")
        (cell_dir / "rows.csv").write_text("email\na@x.example\n", encoding="utf-8")
    return tmp_path / "content", tmp_path / "profiles"


def _coords(cells) -> list[tuple[str, str, str]]:
    return [(c.segment, c.persona, c.signal) for c in cells]


def test_a_declared_first_cell_does_not_end_the_walk_and_every_bucket_is_counted(tmp_path):
    # The declared cell is the FIRST one in matrix order — the case every earlier fixture missed.
    content, profiles = _tenant(tmp_path, specs={"spec-a.md": _spec("CISO × Tide chart drifted")})
    b = backlog(PROFILE, content_root=content, profiles_root=profiles)

    assert b.cells_total == 6 and b.specs_read == 1 and b.declared_pairs == 1
    assert _coords(b.unused) == [
        ("Startup", "CISO", "Kiln log arrived"),
        ("Startup", "CTO", "Kiln log arrived"),  # sorted by signal, not matrix order
        ("Startup", "CTO", "Tide chart drifted"),
    ]
    assert _coords(b.unmapped) == [
        ("Enterprise", "Harbour Master", "Tide chart drifted"),  # sorted, not matrix order
        ("Enterprise", "Kiln Warden", "Tide chart drifted"),
    ]
    assert b.used == 1  # 6 cells − 3 unused − 2 unmapped: an unjoinable row is not "used"


def test_the_report_names_every_bucket_with_its_number(tmp_path):
    content, profiles = _tenant(tmp_path, specs={"spec-a.md": _spec("CISO × Tide chart drifted")})
    text = render_backlog(backlog(PROFILE, content_root=content, profiles_root=profiles))
    lines = text.splitlines()

    assert lines[0] == "HOOK BACKLOG — 1/6 cell(s) argued by 1 spec(s)"
    assert "every cell in the matrix is declared" not in text
    # Unused cells, grouped once under their segment, each with its opening sentence.
    assert lines.count("Startup") == 1
    assert "  CISO × Kiln log arrived" in lines
    assert "      Answering in the log beats answering from memory." in lines
    assert "  CTO × Tide chart drifted" in lines
    # The unjoinable rows are NAMED, with their count, never folded into the segment list.
    assert "UNMAPPED PERSONA — 2 unused cell(s)" in text
    assert "  Enterprise × Harbour Master × Tide chart drifted" in lines
    assert "  Enterprise × Kiln Warden × Tide chart drifted" in lines
    assert "  [drafts included]" not in lines[0]


def test_drafts_fold_in_only_when_asked(tmp_path):
    content, profiles = _tenant(
        tmp_path,
        specs={"spec-a.md": _spec("CISO × Tide chart drifted")},
        drafts={"cto-kiln": _spec("CTO × Kiln log arrived")},
    )
    without = backlog(PROFILE, content_root=content, profiles_root=profiles)
    assert ("Startup", "CTO", "Kiln log arrived") in _coords(without.unused)
    assert without.include_drafts is False and without.specs_read == 1

    with_drafts = backlog(
        PROFILE, content_root=content, profiles_root=profiles, include_drafts=True
    )
    assert ("Startup", "CTO", "Kiln log arrived") not in _coords(with_drafts.unused)
    assert with_drafts.include_drafts is True and with_drafts.specs_read == 2
    assert with_drafts.used == 2 and with_drafts.declared_pairs == 2
    assert render_backlog(with_drafts).splitlines()[0].endswith("  [drafts included]")


def test_used_counts_neither_bucket():
    b = Backlog(cells_total=6)
    b.unused = [object()] * 3  # type: ignore[list-item]
    b.unmapped = [object()] * 2  # type: ignore[list-item]
    assert b.used == 1


def test_the_all_declared_line_appears_only_when_both_buckets_are_empty(tmp_path):
    """`every cell ... is declared` is the report for an EMPTY backlog — never for one that
    merely has no unjoinable rows, and never withheld when there are none."""
    startup_only = _MATRIX.split("## Enterprise")[0]
    three = {
        f"spec-{i}.md": _spec(cell)
        for i, cell in enumerate(
            ["CISO × Tide chart drifted", "CISO × Kiln log arrived", "CTO × Tide chart drifted"]
        )
    }
    content, profiles = _tenant(tmp_path, specs=three, matrix=startup_only)
    text = render_backlog(backlog(PROFILE, content_root=content, profiles_root=profiles))
    assert "every cell in the matrix is declared" not in text
    assert "  CTO × Kiln log arrived" in text.splitlines()

    four = {**three, "spec-3.md": _spec("CTO × Kiln log arrived")}
    content, profiles = _tenant(tmp_path / "all", specs=four, matrix=startup_only)
    b = backlog(PROFILE, content_root=content, profiles_root=profiles)
    assert not b.unused and not b.unmapped and b.used == 4
    text = render_backlog(b)
    assert text.splitlines()[0] == "HOOK BACKLOG — 4/4 cell(s) argued by 4 spec(s)"
    assert "every cell in the matrix is declared by some spec." in text
