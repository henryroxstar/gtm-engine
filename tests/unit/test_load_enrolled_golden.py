"""The golden for ``lanes.context.load_enrolled`` — written BEFORE the reader was split.

``load_enrolled`` is the one "who is in which sequence" reader that routing, the template
pipeline and the status page's "in the sending tool" line all share. The outreach log needs the
same traversal with the deleted sequences left in, so the traversal was extracted and
``load_enrolled`` rebuilt on top of it. This file pins what ``load_enrolled`` returned BEFORE
that edit — people, the order they were first seen, which sequence id each is credited to, and
the exact ``notes`` — so the extraction cannot change routing by accident.

What it deliberately includes (each one is a way a refactor quietly changes the answer):

* the same sequence id registered twice, NON-contiguously, with a person on both lists
  (a map keyed by id and flattened would hand the person to the wrong sequence);
* a person on lists of two different sequences (first registration wins);
* a ``DRAFT-`` id that history also records as deleted (counts as a draft, not a deletion —
  the draft check runs first);
* a deleted registration whose list file is missing (no note, no crash — a deleted list is
  never opened);
* a live registration whose list file is missing (a note naming it, in loop order);
* an id with padding spaces: the deletion check strips it, the stored id does not.

Fictional identities only (§R9); ``.example`` domains.
"""

from __future__ import annotations

import json

from gtm_core.lanes.context import load_enrolled

PROFILE = "acme"

P1 = "ada.north@lantern.example"
P2 = "ben.west@lantern.example"
P3 = "cy.east@riverbend.example"
P4 = "dee.south@riverbend.example"
P5 = "eli.draft@copperline.example"
P6 = "fay.pad@eastvale.example"
P7 = "gus.gone@eastvale.example"


def build_world(tmp_path):
    """A content root whose ``cells.toml`` exercises every branch of the traversal."""
    root = tmp_path / "content"
    seq = root / PROFILE / "prospects" / "sequences"
    seq.mkdir(parents=True)

    def csv(name, *emails):
        (seq / name).write_text(
            "email,company\n" + "".join(f"{e},Co\n" for e in emails), encoding="utf-8"
        )

    csv("a1.csv", P1, P2)
    csv("b.csv", P2, P3)  # P2 is already A's; P3 is B's first
    csv("a2.csv", P3, P4)  # P3 is already B's; P4 is A's — same id as a1, NOT adjacent
    csv("draft.csv", P5)
    csv("pad.csv", P6)
    # gone.csv is deliberately never written: a deleted list is never opened.
    # miss.csv is deliberately never written: a live list that is missing earns a note.
    csv("padgone.csv", P7)

    def reg(sid, csv_name):
        return f'[[sequence]]\nid = "{sid}"\ncsv = "{csv_name}"\nspec = "s.md"\n'

    (seq / "cells.toml").write_text(
        reg("SeqAaaaa1", "a1.csv")
        + reg("SeqBbbbb2", "b.csv")
        + reg("SeqAaaaa1", "a2.csv")
        + reg("DRAFT-x", "draft.csv")
        + reg("DRAFT-both", "draft.csv")
        + reg("Gone0000g", "gone.csv")
        + reg("  Pad02  ", "pad.csv")
        + reg("Miss0000m", "miss.csv")
        + reg(" Padgone1 ", "padgone.csv"),
        encoding="utf-8",
    )
    history = [
        {"event": "sequence_deleted", "sequence_id": "DRAFT-both"},
        {"event": "sequence_deleted", "sequence_id": "Gone0000g"},
        {"event": "sequence_cleanup", "sequences_deleted": [{"id": "Padgone1"}]},
    ]
    (root / PROFILE / "history.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in history), encoding="utf-8"
    )
    return root, seq


def test_load_enrolled_is_unchanged(tmp_path):
    root, seq = build_world(tmp_path)
    enrolled, notes = load_enrolled(PROFILE, root, seq)

    # People in the order they were first seen; each credited to the FIRST registration that
    # named them. P2 -> the first A list (not B); P3 -> B (not the second A list); the padded
    # id is stored exactly as registered, spaces and all.
    assert list(enrolled.items()) == [
        (P1, "SeqAaaaa1"),
        (P2, "SeqAaaaa1"),
        (P3, "SeqBbbbb2"),
        (P4, "SeqAaaaa1"),
        (P6, "  Pad02  "),
    ]

    # Missing-file notes first (loop order), then the DRAFT note, then the deleted note.
    # DRAFT-both is ALSO recorded deleted: the draft check runs first, so it is a draft.
    # Gone0000g's list is missing, but it is deleted, so it earns no missing-file note.
    assert notes == [
        "registered enrolled list for sequence 'Miss0000m' is missing on disk: miss.csv",
        "2 DRAFT-* list(s) in cells.toml were never staged and do not count as enrolled",
        "2 registered list(s) in cells.toml belong to sequences history.jsonl records as "
        "deleted from the provider and do not count as enrolled",
    ]


def test_a_registered_id_is_counted_per_registration_not_per_id(tmp_path):
    """The deleted note counts registrations: one id registered twice and deleted counts 2."""
    root, seq = build_world(tmp_path)
    (seq / "cells.toml").write_text(
        (seq / "cells.toml").read_text(encoding="utf-8")
        + '[[sequence]]\nid = "Gone0000g"\ncsv = "gone2.csv"\nspec = "s.md"\n',
        encoding="utf-8",
    )
    _enrolled, notes = load_enrolled(PROFILE, root, seq)
    assert notes[-1].startswith("3 registered list(s)")


def test_no_cells_toml_and_no_history_is_an_empty_answer(tmp_path):
    root = tmp_path / "content"
    seq = root / PROFILE / "prospects" / "sequences"
    seq.mkdir(parents=True)
    assert load_enrolled(PROFILE, root, seq) == ({}, [])


# ── T2: the by-sequence traversal the outreach log is built on ─────────────────────────────


def test_registrations_come_back_in_file_order_with_a_status_each(tmp_path):
    from gtm_core.lanes.context import (
        REG_DELETED,
        REG_DRAFT,
        REG_MISSING,
        REG_OK,
        load_enrolments_by_sequence,
    )

    root, seq = build_world(tmp_path)
    regs = load_enrolments_by_sequence(PROFILE, root, seq)
    # FILE order, one entry per registration: the same id twice stays twice and stays apart.
    assert [(r.sequence_id, r.csv, r.status) for r in regs] == [
        ("SeqAaaaa1", "a1.csv", REG_OK),
        ("SeqBbbbb2", "b.csv", REG_OK),
        ("SeqAaaaa1", "a2.csv", REG_OK),
        ("DRAFT-x", "draft.csv", REG_DRAFT),
        ("DRAFT-both", "draft.csv", REG_DRAFT),
        ("Gone0000g", "gone.csv", REG_DELETED),
        ("  Pad02  ", "pad.csv", REG_OK),
        ("Miss0000m", "miss.csv", REG_MISSING),
        (" Padgone1 ", "padgone.csv", REG_DELETED),
    ]
    # A person on two lists of one sequence is on BOTH registrations; nothing is collapsed.
    assert regs[0].emails == (P1, P2) and regs[2].emails == (P3, P4)
    # A deleted list is not opened unless asked, and a draft is never opened.
    assert regs[5].emails == () and regs[8].emails == () and regs[3].emails == ()


def test_include_deleted_reads_a_deleted_list_that_exists_and_tolerates_one_that_does_not(
    tmp_path,
):
    from gtm_core.lanes.context import load_enrolments_by_sequence

    root, seq = build_world(tmp_path)
    regs = load_enrolments_by_sequence(PROFILE, root, seq, include_deleted=True)
    assert regs[8].emails == (P7,)  # padgone.csv exists
    assert regs[5].emails == ()  # gone.csv does not: no crash, no invented people
    assert regs[4].status == "draft"  # a draft is still a draft even when history deleted it


def test_a_caller_can_inject_the_reader_and_the_registrations(tmp_path):
    """The log's strict, confined readers go into the ONE traversal; routing passes none."""
    from gtm_core.lanes.context import load_enrolments_by_sequence

    root, seq = build_world(tmp_path)
    seen: list[str] = []

    def strict(path):
        seen.append(path.name)
        return ("only.strict@lantern.example",)

    regs = load_enrolments_by_sequence(
        PROFILE,
        root,
        seq,
        registrations=[
            {"sequence_id": "SeqAaaaa1", "csv": "a1.csv", "spec": "s.md"},
            {"sequence_id": "DRAFT-z", "csv": "draft.csv", "spec": "s.md"},
        ],
        read_list=strict,
        deleted_ids=set(),
    )
    assert [r.status for r in regs] == ["ok", "draft"]
    assert regs[0].emails == ("only.strict@lantern.example",) and seen == ["a1.csv"]
