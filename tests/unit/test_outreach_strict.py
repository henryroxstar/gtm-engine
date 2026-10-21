"""The outreach log's strict readers: refuse a corrupt input, name the file, never read less.

Routing's readers are lenient on purpose (see ``gtm_core/outreach_strict.py``); the log's are
strict on purpose. The paired test at the bottom feeds the SAME corrupt input to both and
requires the two different answers, so the divergence is a decision a test holds rather than an
accident someone can "fix". Fictional identities only (§R9).
"""

from __future__ import annotations

import json

import pytest

from gtm_core import outreach_strict as strict
from gtm_core.outreach_strict import StrictInputError

PROFILE = "acme"


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "content"
    seq = root / PROFILE / "prospects" / "sequences"
    (seq / ".pool").mkdir(parents=True)
    return root, seq


def cells(seq, body: str) -> None:
    (seq / "cells.toml").write_text(body, encoding="utf-8")


def reg(sid, csv="a.csv", spec="s.md", **extra) -> str:
    more = "".join(f'{k} = "{v}"\n' for k, v in extra.items())
    return f'[[sequence]]\nid = "{sid}"\ncsv = "{csv}"\nspec = "{spec}"\n{more}'


# ── T4: the shape the live file really has, and the one that is genuinely wrong ───────────


def test_one_sequence_fed_by_several_lists_is_normal(world):
    root, seq = world
    cells(seq, reg("SeqAaaaa1", "a.csv") + reg("SeqAaaaa1", "b.csv") + reg("SeqBbbbb2", "c.csv"))
    out = strict.read_cells(PROFILE, root)
    assert out.present and [r["sequence_id"] for r in out.registrations] == [
        "SeqAaaaa1",
        "SeqAaaaa1",
        "SeqBbbbb2",
    ]


def test_one_sequence_with_two_different_specs_is_refused(world):
    root, seq = world
    cells(seq, reg("SeqAaaaa1", "a.csv", "one.md") + reg("SeqAaaaa1", "b.csv", "two.md"))
    with pytest.raises(StrictInputError, match="two different specs"):
        strict.read_cells(PROFILE, root)


def test_a_row_missing_id_csv_or_spec_is_counted_not_hidden(world):
    root, seq = world
    cells(seq, reg("SeqAaaaa1") + '[[sequence]]\nid = "NoCsv0000"\nspec = "s.md"\n')
    out = strict.read_cells(PROFILE, root)
    assert len(out.registrations) == 1 and out.skipped == 1


def test_no_cells_toml_is_absence_not_an_error(world):
    root, _seq = world
    out = strict.read_cells(PROFILE, root)
    assert (out.present, out.registrations) == (False, [])


# ── T5: every structural input is refused, and named ──────────────────────────────────────


def test_corrupt_cells_toml_is_refused(world):
    root, seq = world
    cells(seq, "[[sequence]\nthis is not toml")
    with pytest.raises(StrictInputError, match="cells.toml cannot be read"):
        strict.read_cells(PROFILE, root)


def test_cells_entries_that_are_not_tables_are_refused(world):
    root, seq = world
    cells(seq, 'sequence = ["not", "tables"]\n')
    with pytest.raises(StrictInputError, match="not tables"):
        strict.read_cells(PROFILE, root)


def test_a_closed_out_file_that_is_not_utf8_is_refused(world):
    root, seq = world
    (seq / "contacted-2026-10-06.csv").write_bytes(b"email,name\nada@lantern.example,Ad\xe9\xff\n")
    with pytest.raises(StrictInputError, match="contacted-2026-10-06.csv"):
        strict.read_contacted(PROFILE, root)


def test_a_closed_out_file_without_an_email_column_is_refused(world):
    root, seq = world
    (seq / "contacted-2026-10-06.csv").write_text("name,outcome\nAda,replied\n", encoding="utf-8")
    with pytest.raises(StrictInputError, match="no 'email' column"):
        strict.read_contacted(PROFILE, root)


def test_a_sequence_cell_with_two_values_is_refused_not_truncated(world):
    root, seq = world
    (seq / "contacted-2026-10-06.csv").write_text(
        "email,sequences,outcome\nada@lantern.example,SeqAaaaa1;SeqBbbbb2,replied\n",
        encoding="utf-8",
    )
    with pytest.raises(StrictInputError, match="more than one value"):
        strict.read_contacted(PROFILE, root)


def test_a_truncated_history_line_is_refused_with_its_line_number(world):
    root, _seq = world
    (root / PROFILE / "history.jsonl").write_text(
        json.dumps({"event": "x"}) + '\n{"event": "sequence_deleted", "sequ\n', encoding="utf-8"
    )
    with pytest.raises(StrictInputError, match="line 2"):
        strict.read_history_facts(PROFILE, root)


def test_an_unreadable_snapshot_is_refused_but_an_absent_one_is_not(world):
    root, seq = world
    assert strict.read_snapshot(PROFILE, root).present is False
    (seq / ".pool" / "sequence-stats.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(StrictInputError, match="cannot be read"):
        strict.read_snapshot(PROFILE, root)


@pytest.mark.parametrize(
    "rows, message",
    [
        ([["not", "an", "object"]], "not an object"),
        ([{"sequenceName": "no id"}], "no sequenceId"),
        ([{"sequenceId": "SeqAaaaa1"}, {"sequenceId": "SeqAaaaa1"}], "appears twice"),
    ],
)
def test_a_malformed_snapshot_row_is_refused(world, rows, message):
    root, seq = world
    (seq / ".pool" / "sequence-stats.json").write_text(
        json.dumps({"fetched": "2026-10-07T08:00:00Z", "sequences": rows}), encoding="utf-8"
    )
    with pytest.raises(StrictInputError, match=message):
        strict.read_snapshot(PROFILE, root)


# ── T6: a list or spec path cannot leave the sequences folder ─────────────────────────────


@pytest.mark.parametrize("bad", ["../outside.csv", "/etc/hosts", "sub/../../escape.csv"])
def test_a_list_path_that_leaves_the_folder_is_refused(world, bad):
    root, seq = world
    cells(seq, reg("SeqAaaaa1", csv=bad))
    with pytest.raises(StrictInputError, match="outside the sequences folder"):
        strict.read_cells(PROFILE, root)


def test_a_symlink_out_of_the_tree_is_refused(world, tmp_path):
    root, seq = world
    outside = tmp_path / "elsewhere.csv"
    outside.write_text("email\nada@lantern.example\n", encoding="utf-8")
    (seq / "linked.csv").symlink_to(outside)
    cells(seq, reg("SeqAaaaa1", csv="linked.csv"))
    with pytest.raises(StrictInputError, match="outside the sequences folder"):
        strict.read_cells(PROFILE, root)


def test_a_nul_byte_in_a_path_is_refused_not_a_crash(world):
    root, seq = world
    cells(seq, reg("SeqAaaaa1", csv="a\\u0000.csv"))
    with pytest.raises(StrictInputError):
        strict.read_cells(PROFILE, root)


def test_the_strict_list_reader_refuses_a_non_utf8_list_and_a_foreign_path(world, tmp_path):
    _root, seq = world
    read = strict.read_list_strict(seq)
    (seq / "bad.csv").write_bytes(b"email\nada@lantern.example\n\xff\xfe\n")
    with pytest.raises(StrictInputError, match="cannot be read"):
        read(seq / "bad.csv")
    other = tmp_path / "foreign.csv"
    other.write_text("email\nx@lantern.example\n", encoding="utf-8")
    with pytest.raises(StrictInputError, match="outside the sequences folder"):
        read(other)
    (seq / "good.csv").write_text("email,co\n  Ada@Lantern.Example ,Co\n,Co\n", encoding="utf-8")
    assert read(seq / "good.csv") == ("ada@lantern.example",)


# ── T10: both closed-out layouts, and what happens when files disagree ────────────────────


def test_the_newer_layout_carries_its_outcome_and_sequence(world):
    root, seq = world
    (seq / "contacted-2026-10-06.csv").write_text(
        "email,name,company_domain,sequences,max_step,outcome,source\n"
        "ada@lantern.example,Ada,lantern.example,SeqAaaaa1,2,no-reply,saleshandy\n"
        "ben@lantern.example,Ben,lantern.example,,1,unsubscribed,manual\n"
        "cy@lantern.example,Cy,lantern.example,manual:generic,1,wat,manual\n",
        encoding="utf-8",
    )
    got = strict.read_contacted(PROFILE, root)
    assert got["ada@lantern.example"].outcome == "no-reply"
    assert got["ada@lantern.example"].tokens == ("SeqAaaaa1",)
    assert got["ben@lantern.example"].outcome == "unsubscribed"
    # An unknown outcome word is still a contact: closed out, just not classifiable.
    assert got["cy@lantern.example"].outcome == "unclassified"


def test_the_older_layout_derives_its_outcome_from_its_flags(world):
    root, seq = world
    (seq / "contacted-2026-08-11.csv").write_text(
        "email,first,last,company_domain,sequence,sender,sent_at_utc,replied,sentiment,"
        "unsubscribed,bounced,disposition\n"
        "a@lantern.example,A,A,lantern.example,generic,me,t,No,x,No,No,exclude\n"
        "b@lantern.example,B,B,lantern.example,generic,me,t,Yes,x,No,No,exclude\n"
        "c@lantern.example,C,C,lantern.example,generic,me,t,Yes,x,YES,No,dnc\n"
        "d@lantern.example,D,D,lantern.example,generic,me,t,No,x,No,Yes,exclude\n"
        "e@lantern.example,E,E,lantern.example,generic,me,t,?,x,No,No,exclude\n",
        encoding="utf-8",
    )
    got = strict.read_contacted(PROFILE, root)
    assert {k[0]: v.outcome for k, v in got.items()} == {
        "a": "no-reply",
        "b": "replied",
        "c": "unsubscribed",  # unsubscribed outranks replied
        "d": "bounced",
        "e": "unclassified",  # a flag nobody can read is not 'no reply'
    }
    assert got["a@lantern.example"].tokens == ("generic",)


def test_a_person_in_two_files_keeps_the_strongest_outcome_and_every_source(world):
    root, seq = world
    (seq / "contacted-2026-08-11.csv").write_text(
        "email,sequence,replied,unsubscribed,bounced\nada@lantern.example,generic,No,Yes,No\n",
        encoding="utf-8",
    )
    (seq / "contacted-2026-10-06.csv").write_text(
        "email,sequences,outcome\nada@lantern.example,manual:generic,no-reply\n", encoding="utf-8"
    )
    got = strict.read_contacted(PROFILE, root)["ada@lantern.example"]
    assert got.outcome == "unsubscribed"  # not demoted to the newer file's no-reply
    assert got.sources == ("contacted-2026-08-11.csv", "contacted-2026-10-06.csv")
    assert got.tokens == ("generic", "manual:generic")


@pytest.mark.parametrize(
    "token, known, expected",
    [
        ("SeqAaaaa01", {"SeqAaaaa01"}, True),  # provider-shaped (10 chars) and known
        ("SeqZzzzz09", {"SeqAaaaa01"}, False),  # provider-shaped, unknown: no phantom sequence
        ("enterprise", {"SeqAaaaa01"}, False),  # a 10-letter label is provider-shaped too
        ("manual:generic", {"manual:generic"}, False),
        ("", {""}, False),
    ],
)
def test_only_a_known_provider_shaped_token_names_a_sequence(token, known, expected):
    assert strict.provider_token(token, known) is expected


# ── the snapshot: absent is not zero ──────────────────────────────────────────────────────


def test_snapshot_counters_are_none_when_absent_and_zero_only_when_zero(world):
    root, seq = world
    rows = [
        {
            "sequenceId": "SeqAaaaa1",
            "sequenceName": "A",
            "status": "Paused",
            "prospects": [{"total": "47", "contacted": "0"}],
            "emails": {"status": {"delivered": 0}},
        },
        {
            "sequenceId": "SeqBbbbb2",
            "sequenceName": "B",
            "prospects": [{"total": "9"}],
            "emails": {"status": {"delivered": 3}},
        },
        {"sequenceId": "SeqCcccc3", "sequenceName": "C", "prospects": [{"total": " "}]},
    ]
    (seq / ".pool" / "sequence-stats.json").write_text(
        json.dumps(
            {
                "fetched": "2026-10-07T08:00:00Z",
                "sequences": rows,
                "stamps": {"SeqAaaaa1": "2026-10-06T08:00:00Z"},
            }
        ),
        encoding="utf-8",
    )
    snap = strict.read_snapshot(PROFILE, root)
    a, b, c = (snap.rows[k] for k in ("SeqAaaaa1", "SeqBbbbb2", "SeqCcccc3"))
    assert (a.total, a.contacted, a.status) == (47, 0, "paused")  # a REAL zero
    assert a.stamp == "2026-10-06T08:00:00Z"  # its own date
    assert (b.total, b.contacted, b.status) == (9, None, None)  # absent, and no flag
    assert b.stamp == "2026-10-07T08:00:00Z"  # falls back to the file's date
    assert (c.total, c.contacted) == (None, None)  # a blank is not a zero


def test_history_facts_read_both_deletion_shapes_and_every_known_id(world):
    root, _seq = world
    rows = [
        {"event": "sequence_deleted", "sequence_id": "Gone000001"},
        {"event": "sequence_cleanup", "sequences_deleted": [{"id": "Swept00001", "title": "t"}]},
        {"event": "sequence_staged", "sequence_id": "Live000001"},
        {"event": "campaign_closed", "sequence_ids": ["Closed0001"]},
    ]
    (root / PROFILE / "history.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows) + "\n", encoding="utf-8"
    )
    facts = strict.read_history_facts(PROFILE, root)
    assert facts.deleted == {"Gone000001", "Swept00001"}
    assert facts.known >= {"Gone000001", "Swept00001", "Live000001", "Closed0001"}


def test_enrolments_are_lenient_like_their_other_reader(world):
    root, _seq = world
    path = root / PROFILE / "prospects" / "enrolments.jsonl"
    path.write_text(
        json.dumps({"email": " Ada@Lantern.Example ", "sequence_id": "SeqAaaaa1"})
        + "\nnot json\n"
        + json.dumps({"email": "", "sequence_id": "x"})
        + "\n",
        encoding="utf-8",
    )
    assert strict.read_enrolments(PROFILE, root) == ([("ada@lantern.example", "SeqAaaaa1")], 2)


# ── the pairing: the SAME corrupt input, two deliberately different answers ───────────────


def test_routing_stays_lenient_on_input_the_log_refuses(world):
    """If someone 'fixes' the log to tolerate this, or routing to refuse it, one of the two
    halves fails: the divergence is a decision, not an accident."""
    from gtm_core.cells import load_cell_map
    from gtm_core.lanes.context import _load_contacted, load_enrolled

    root, seq = world
    cells(seq, "[[sequence]\nthis is not toml")
    (seq / "contacted-2026-10-06.csv").write_bytes(b"email\nada@lantern.example\nb\xff@x.example\n")

    # routing: no registrations, and the undecodable bytes are dropped rather than refused
    assert load_cell_map(PROFILE, root) == []
    assert load_enrolled(PROFILE, root, seq) == ({}, [])
    assert "ada@lantern.example" in _load_contacted(seq)

    # the log: both refused, by name
    with pytest.raises(StrictInputError):
        strict.read_cells(PROFILE, root)
    with pytest.raises(StrictInputError):
        strict.read_contacted(PROFILE, root)


def test_a_closed_out_file_that_is_a_link_out_of_the_tree_is_refused(world, tmp_path):
    root, seq = world
    outside = tmp_path / "elsewhere.csv"
    outside.write_text("email,outcome\nada@lantern.example,replied\n", encoding="utf-8")
    (seq / "contacted-2026-10-06.csv").symlink_to(outside)
    with pytest.raises(StrictInputError, match="outside the sequences folder"):
        strict.read_contacted(PROFILE, root)
