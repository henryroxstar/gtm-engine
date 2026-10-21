"""The outreach log's person rows: membership, the closed vocabularies, and the findings.

Every case runs on the small fictional world in ``_outreach_world.py`` with the Saleshandy
figures written by the real snapshot writer. Each test names the wrong answer it exists to catch.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from gtm_core.outreach_findings import CODE_EMAILED, CODE_GONE, clean
from gtm_core.outreach_model import (
    BOOKKEEPING_REASONS,
    EMAILED_VALUES,
    PERSON_STATUSES,
    SEQUENCE_STATES,
    state_words,
)
from gtm_core.outreach_people import build_view
from gtm_core.outreach_strict import StrictInputError

from ._outreach_world import (
    ABSENT,
    GONE,
    LIVE,
    NOW,
    PAUSED,
    PROFILE,
    STAGED,
    P,
    make_world,
    write_snapshot,
)


@pytest.fixture
def world(tmp_path):
    return make_world(tmp_path)


def rows(view, kind="sequence", sid=None):
    return [r for r in view.rows if r["kind"] == kind and (sid is None or r["sequence_id"] == sid)]


def one(view, who, sid=None):
    found = [r for r in rows(view, sid=sid) if r["email_address"] == P[who]]
    assert len(found) == 1, (who, sid, len(found))
    return found[0]


# ── T3: membership is registered lists + closed-out files + the dispatcher ledger ─────────


def test_membership_is_the_union_of_every_record(world):
    root, _seq = world
    v = build_view(PROFILE, root, now=NOW)
    live = {r["email_address"] for r in rows(v, sid=LIVE)}
    # two registered lists for one sequence, plus a person only the dispatcher's ledger knows
    assert live == {P[k] for k in ("ada", "ben", "cy", "dee", "pat")}
    # closed-out people whose sequence cell names no known sequence become 'contacted' rows
    assert {r["email_address"] for r in rows(v, "contacted")} == {P["zed"], P["yan"]}
    # a closed-out person WITH a known provider token gets a sequence row, not a contacted one
    assert one(v, "eli", PAUSED)["person_status"] == "closed-out"
    assert any("recognises" in n for n in v.notes)  # the unknown ten-character token is counted


def test_a_person_on_two_sequences_has_two_rows_but_is_one_person(world):
    root, seq = world
    (seq / "c.csv").write_text(
        (seq / "c.csv").read_text(encoding="utf-8")
        + "ada@lantern.example,Ada,Test,Lantern Co,CTO,lantern.example,X,Y,a clause\n",
        encoding="utf-8",
    )
    v = build_view(PROFILE, root, now=NOW)
    assert {r["sequence_id"] for r in rows(v) if r["email_address"] == P["ada"]} == {LIVE, STAGED}
    assert v.people == len({r["email_address"] for r in rows(v)})  # people are counted once


def test_a_draft_registration_creates_no_rows(world):
    root, _seq = world
    v = build_view(PROFILE, root, now=NOW)
    assert not [r for r in v.rows if r["sequence_id"].startswith("DRAFT")]


# ── T7: closed vocabularies; an unreadable outcome still closes the person out ────────────


def test_every_emitted_value_is_in_its_closed_list(world):
    root, _seq = world
    v = build_view(PROFILE, root, now=NOW)
    words = set(state_words().values())
    for r in v.rows:
        assert r["person_status"] in PERSON_STATUSES
        assert r["emailed"] in EMAILED_VALUES
        if r["kind"] == "sequence":
            assert r["sequence_state"] in words
    assert set(state_words()) == set(SEQUENCE_STATES)


def test_an_unknown_outcome_word_is_still_a_closed_out_person(world):
    root, seq = world
    path = seq / "contacted-2026-10-06.csv"
    path.write_text(
        path.read_text(encoding="utf-8")
        + f"{P['ned']},Ned,southfield.example,{ABSENT},1,wat,manual\n",
        encoding="utf-8",
    )
    r = one(build_view(PROFILE, root, now=NOW), "ned")
    assert (r["person_status"], r["outcome"]) == ("closed-out", "unclassified")


# ── T8: never downgraded; a live re-enrolment stays visible ───────────────────────────────


def test_closed_out_is_never_downgraded_by_a_later_deletion(world):
    root, _seq = world
    v = build_view(PROFILE, root, now=NOW)
    lee, max_ = one(v, "lee"), one(v, "max")
    assert lee["sequence_state"] == "Deleted" and lee["person_status"] == "closed-out"
    # the SAME sequence, but this person was never closed out: the cautious default
    assert max_["person_status"] == "sequence-deleted-sends-unrecorded"


def test_a_closed_out_person_on_a_sending_sequence_shows_both_facts(world):
    root, _seq = world
    cy = one(build_view(PROFILE, root, now=NOW), "cy", LIVE)
    assert cy["person_status"] == "closed-out"  # they were emailed already ...
    assert cy["sequence_state"] == state_words()["active"]  # ... and are on a sending sequence


# ── T9: precedence, bookkeeping reasons, and the ledger row that hides behind another ─────


def test_bookkeeping_reasons_never_set_a_status_but_everything_else_does(world):
    root, _seq = world
    v = build_view(PROFILE, root, now=NOW)
    assert BOOKKEEPING_REASONS == {
        "cross-sequence-overlap",
        "contacted-no-reply",
        "contacted-bounced",
    }
    gus = one(v, "gus")
    assert (gus["person_status"], gus["suppression_reason"]) == (
        "in-sequence",
        "cross-sequence-overlap",
    )
    assert one(v, "ben")["person_status"] == "suppressed"
    # free text and an unknown word fail toward 'suppressed', never toward harmless
    assert one(v, "jo")["person_status"] == "suppressed"
    assert one(v, "jo")["suppression_reason"].startswith("Adjudicated")


def test_a_harmless_ledger_row_cannot_hide_a_real_opt_out_behind_it(world):
    """hal has a bookkeeping row FIRST and a real opt-out second; the index's first-wins match
    would have reported only the first and let hal read as 'in-sequence'."""
    root, _seq = world
    hal = one(build_view(PROFILE, root, now=NOW), "hal")
    assert (hal["person_status"], hal["suppression_reason"]) == ("suppressed", "dnc-optout")


def test_a_company_level_suppression_covers_its_people(world):
    root, _seq = world
    kim = one(build_view(PROFILE, root, now=NOW), "kim")
    assert (kim["person_status"], kim["suppression_reason"]) == ("suppressed", "competitor")


def test_closed_out_outranks_suppression(world):
    root, seq = world
    ledger = seq / ".pool" / "suppression.csv"
    ledger.write_text(
        ledger.read_text(encoding="utf-8") + f"{P['eli']},,,dnc-optout,2026-10-07,\n",
        encoding="utf-8",
    )
    eli = one(build_view(PROFILE, root, now=NOW), "eli")
    assert (eli["person_status"], eli["suppression_reason"]) == ("closed-out", "dnc-optout")


# ── T11: 'emailed' is never inferred from the state of a sequence ─────────────────────────


def test_emailed_is_yes_no_or_unknown_and_never_guessed(world):
    root, _seq = world
    v = build_view(PROFILE, root, now=NOW)
    assert one(v, "cy")["emailed"] == "yes"  # in a closed-out list
    assert one(v, "ida")["emailed"] == "no"  # fresh row, counter present and zero
    # '3 of 4 emailed' says nothing about WHICH three: gus is not in any list
    assert one(v, "gus")["emailed"] == "unknown"
    assert one(v, "ada")["emailed"] == "unknown"  # a sending sequence is not proof either way


# ── T12: the state words, staleness, and a missing flag or counter ────────────────────────


def test_state_words_come_from_the_pages_own_vocabulary(world):
    from gtm_core.prospect_lede import GO_LIVE_WORDS

    root, _seq = world
    v = build_view(PROFILE, root, now=NOW)
    assert one(v, "ada")["sequence_state"] in GO_LIVE_WORDS["active"]
    assert one(v, "ida")["sequence_state"] in GO_LIVE_WORDS["staged"]  # 'Loaded, not started'
    assert one(v, "eli")["sequence_state"] in GO_LIVE_WORDS["paused"]


def test_an_old_row_reads_status_unknown_and_proves_nothing(world):
    root, _seq = world
    v = build_view(PROFILE, root, now=NOW + timedelta(days=20))
    states = {r["sequence_state"] for r in rows(v) if r["sequence_id"] in (LIVE, PAUSED, STAGED)}
    assert states == {state_words()["unknown"]}
    # a counter of zero in an old row must not become 'not emailed'
    assert one(v, "ida")["emailed"] == "unknown"


def test_no_snapshot_file_reads_every_sequence_unknown(tmp_path):
    root, _seq = make_world(tmp_path, snapshot=False)
    v = build_view(PROFILE, root, now=NOW)
    assert not v.snapshot_present and any("figures missing" in n for n in v.notes)
    live = [r for r in rows(v) if r["sequence_id"] in (LIVE, PAUSED, STAGED)]
    assert {r["sequence_state"] for r in live} == {state_words()["unknown"]}
    assert v.findings == [] and v.not_checkable > 0  # nothing could be checked, and it says so


def test_a_sequence_missing_from_the_figures_is_named_not_assumed(world):
    root, _seq = world
    assert (
        one(build_view(PROFILE, root, now=NOW), "ned")["sequence_state"]
        == state_words()["not-in-snapshot"]
    )


def test_a_paused_flag_with_no_counter_is_unknown_not_not_started(world):
    root, _seq = world
    stats = root / PROFILE / "prospects" / "sequences" / ".pool" / "sequence-stats.json"
    doc = json.loads(stats.read_text(encoding="utf-8"))
    for s in doc["sequences"]:
        if s["sequenceId"] == STAGED:
            s["prospects"][0].pop("contacted")
    stats.write_text(json.dumps(doc), encoding="utf-8")
    ida = one(build_view(PROFILE, root, now=NOW), "ida")
    assert ida["sequence_state"] == state_words()["unknown"] and ida["emailed"] == "unknown"


# ── T13: a deleted sequence splits on the provider's own counter ──────────────────────────


def test_a_deleted_sequence_with_a_fresh_zero_counter_is_no_sends(tmp_path):
    root, _seq = make_world(tmp_path, snapshot=False)
    write_snapshot(root, rows=[(LIVE, 5, 1, True), (GONE, 2, 0, False)])
    assert (
        one(build_view(PROFILE, root, now=NOW), "max")["person_status"]
        == "sequence-deleted-no-sends"
    )


# ── T15: the email text ───────────────────────────────────────────────────────────────────


def test_each_person_gets_their_own_text_and_a_same_thread_follow_up(world):
    root, _seq = world
    v = build_view(PROFILE, root, now=NOW)
    ada, ben = one(v, "ada", LIVE), one(v, "ben", LIVE)
    assert ada["touch_1_subject"] == "a note for Lantern Co"
    assert "Hi Ada," in ada["touch_1_body"] and "Hi Ben," in ben["touch_1_body"]
    assert ada["touch_2_subject"] == "(reply in the same thread)"
    assert ada["touch_3_body"] == "" and ada["followup_touches_in_ladder"] == 1


def test_a_pack_row_is_shown_as_written(world):
    root, _seq = world
    pack = rows(build_view(PROFILE, root, now=NOW), "pack")[0]
    assert (pack["touch_1_subject"], pack["person_status"]) == ("hello there", "drafted")
    assert "the body" in pack["touch_1_body"]


def test_an_empty_merge_field_is_written_as_unavailable_not_as_hi_comma(world):
    root, seq = world
    text = (seq / "a.csv").read_text(encoding="utf-8").replace("Ada,Test", ",Test", 1)
    (seq / "a.csv").write_text(text, encoding="utf-8")
    v = build_view(PROFILE, root, now=NOW)
    ada = one(v, "ada", LIVE)
    assert ada["touch_1_body"].startswith("UNAVAILABLE: merge-field First Name")
    assert "Hi ," not in ada["touch_1_body"]
    assert any("First Name" in p for p in v.problems)  # a sending sequence: worth a non-zero exit


def test_a_missing_spec_or_one_with_too_many_steps_is_refused_not_truncated(world):
    root, seq = world
    steps = "".join(
        f"**Step {n} — Day {n}** · Subject: `s{n}`\n\n> body {n}\n\n" for n in range(1, 5)
    )
    (seq / "spec.md").write_text("# spec\n\n" + steps, encoding="utf-8")
    ada = one(build_view(PROFILE, root, now=NOW), "ada", LIVE)
    assert "has 4 steps" in ada["touch_1_body"] and ada["touch_2_body"] == ""
    (seq / "spec.md").unlink()
    v = build_view(PROFILE, root, now=NOW)
    assert "spec.md is missing" in one(v, "ada", LIVE)["touch_1_body"]


def test_text_defects_in_a_dead_sequence_do_not_count_as_problems(world):
    root, seq = world
    (seq / "spec.md").unlink()
    v = build_view(PROFILE, root, now=NOW)
    names = " ".join(v.problems)
    assert f"Name {LIVE}" in names  # a sending sequence
    assert f"Name {GONE}" not in names and f"Name {ABSENT}" not in names  # deleted / unmeasured


def test_a_person_on_no_registered_list_is_told_so_and_is_not_a_defect(world):
    root, _seq = world
    v = build_view(PROFILE, root, now=NOW)
    pat = one(v, "pat", LIVE)
    assert pat["touch_1_body"].startswith("UNAVAILABLE: this person is on no registered list")
    assert not any("no registered list" in p for p in v.problems)


# ── T17: the close-out findings ───────────────────────────────────────────────────────────


def test_findings_fire_only_when_a_sequence_stopped_after_sending_unrecorded(world):
    root, _seq = world
    v = build_view(PROFILE, root, now=NOW)
    by = {f.sequence_id: f.code for f in v.findings}
    assert by == {PAUSED: CODE_EMAILED, GONE: CODE_GONE}  # LIVE is sending; STAGED sent nothing
    assert v.not_checkable == 2  # the deleted sequence never in the figures, and the absent one


def test_a_fully_covered_sequence_is_silent(world):
    root, seq = world
    path = seq / "contacted-2026-10-06.csv"
    path.write_text(
        path.read_text(encoding="utf-8")
        + f"{P['gus']},Gus,riverbend.example,{PAUSED},1,no-reply,saleshandy\n",
        encoding="utf-8",
    )
    # 3 emailed, 3 closed out (exactly N): nothing is due
    assert PAUSED not in {f.sequence_id for f in build_view(PROFILE, root, now=NOW).findings}


def test_the_warning_words_are_pinned_and_a_name_cannot_forge_a_line(world):
    root, _seq = world
    v = build_view(PROFILE, root, now=NOW)
    text = {f.sequence_id: f.message for f in v.findings}
    assert text[PAUSED] == (
        f"Sequence 'Name {PAUSED}' is not sending, and Saleshandy says it emailed 3 people, "
        "but only 2 of its people are in a closed-out list. Before you delete it: in Saleshandy "
        "open the sequence, export its prospects with their status, and save that as "
        "contacted-2026-10-08.csv in prospects/sequences/ with the columns email, name, "
        "company_domain, sequences, max_step, outcome, source. Then run the log again."
    )
    assert text[GONE].startswith(
        f"Sequence 'Name {GONE}' is gone from Saleshandy. Its last figures"
    )
    assert "\n" not in clean("a\nERROR forged line\r\x00") and len(clean("x" * 500)) <= 120


# ── T29: the identity key ─────────────────────────────────────────────────────────────────


def test_case_and_spacing_do_not_split_one_person(world):
    root, seq = world
    (seq / "a2.csv").write_text(
        "email,first,last,company\n  ADA@Lantern.Example ,Ada,Test,Lantern Co\n", encoding="utf-8"
    )
    v = build_view(PROFILE, root, now=NOW)
    assert len([r for r in rows(v, sid=LIVE) if r["email_address"] == P["ada"]]) == 1


def test_a_plus_address_is_a_different_key_and_is_not_matched(world):
    """Documented, not hidden: ada+tag@ is not ada@. If this ever changes, the PRD's list of
    identity evasions must change with it."""
    root, seq = world
    path = seq / "contacted-2026-10-06.csv"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "ada+tag@lantern.example,Ada,lantern.example,,1,replied,manual\n",
        encoding="utf-8",
    )
    ada = one(build_view(PROFILE, root, now=NOW), "ada", LIVE)
    assert ada["person_status"] == "in-sequence"


# ── refusals ──────────────────────────────────────────────────────────────────────────────


def test_a_corrupt_input_refuses_the_whole_build_by_name(world):
    root, seq = world
    (seq / "cells.toml").write_text("[[sequence]\nnot toml", encoding="utf-8")
    with pytest.raises(StrictInputError, match="cells.toml"):
        build_view(PROFILE, root, now=NOW)


def test_figures_that_list_sequences_with_no_cells_toml_is_refused(world):
    root, seq = world
    (seq / "cells.toml").unlink()
    with pytest.raises(StrictInputError, match="cells.toml is missing"):
        build_view(PROFILE, root, now=NOW)


def test_no_cells_toml_and_no_figures_lists_only_the_packs(tmp_path):
    root, seq = make_world(tmp_path, snapshot=False)
    (seq / "cells.toml").unlink()
    for f in seq.glob("contacted-*.csv"):
        f.unlink()
    (root / PROFILE / "prospects" / "enrolments.jsonl").unlink()
    v = build_view(PROFILE, root, now=NOW)
    assert {r["kind"] for r in v.rows} == {"pack"} and any("No cells.toml" in n for n in v.notes)


# ── T23: the log agrees with the page's own "in the sending tool" split ───────────────────


def test_the_log_projects_onto_the_pages_three_buckets_exactly(world):
    """The oracle is the PAGE's function, not the log's: if the log drifts from it this fails.
    One person is on two sequences (first registration wins on both sides); 'Loaded, not
    started' and 'Paused' both belong to the page's 'paused' bucket; a sequence missing from the
    figures is 'unknown'."""
    from gtm_core.email_campaign_dashboard.delivery import sending_split_for
    from gtm_core.lanes.context import load_enrolled
    from gtm_core.prospects_dashboard import load_sequence_snapshot

    root, seq = world
    (seq / "c.csv").write_text(
        (seq / "c.csv").read_text(encoding="utf-8")
        + "ada@lantern.example,Ada,Test,Lantern Co,CTO,lantern.example,X,Y,a clause\n",
        encoding="utf-8",
    )
    v = build_view(PROFILE, root, now=NOW)
    enrolled, _notes = load_enrolled(PROFILE, root, seq)
    routed = [{"status": "in_sending_tool", "email": e} for e in enrolled]
    page = sending_split_for(
        PROFILE, root, routed, {"sequences": load_sequence_snapshot(PROFILE, root)["rows"]}
    )
    assert set(page) == {"sending", "paused", "unknown"}  # the fixture really spans all three
    assert v.sending_split(list(enrolled)) == page


def test_a_sending_sequence_stays_silent_even_when_sends_are_unrecorded(world):
    """Nothing is due until a sequence STOPS: it is still sending, and the operator cannot delete
    what is running. Three emailed against one closed-out person would warn on a paused sequence
    (see the findings test); on a sending one it must not.
    Catches: a close-out check that ignores the sequence's state."""
    import json

    root, _seq = world
    stats = root / PROFILE / "prospects" / "sequences" / ".pool" / "sequence-stats.json"
    doc = json.loads(stats.read_text(encoding="utf-8"))
    for row in doc["sequences"]:
        if row["sequenceId"] == LIVE:
            row["prospects"][0]["contacted"] = "3"
    stats.write_text(json.dumps(doc), encoding="utf-8")
    v = build_view(PROFILE, root, now=NOW)
    assert LIVE not in {f.sequence_id for f in v.findings}
    assert one(v, "ada", LIVE)["sequence_state"] == state_words()["active"]
