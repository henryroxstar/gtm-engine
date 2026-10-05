"""The reconciler: live sequencer state against history.jsonl and cells.toml.

Fictional data only (§R9). Sequence ids are invented ten-character ids; the SHAPES are copied from
live replies (``get_sequence_stats``: the full ``prospects[0]`` / ``emails.status`` key set, counters
as digit strings in one block and integers in the other; ``list_sequences``: ``{message, payload:
[{id, title, active, steps}]}``; history rows: the event shapes the ledger really holds), and the
counts in the incident test (58 / 142 / 35 held; 1 / 24 / 9 staged; 57 / 118 / 26 unrecorded) are
the ones the 2026-10-02 post-mortem measured. The point of that test is not the numbers: it is that
the id every reader joins on stays the same string from the sequencer to the ledger to cells.toml.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gtm_core import sequencer_reconcile as rec
from gtm_core.cells import load_cell_map
from gtm_core.sequencer_history import index_history
from gtm_core.sequencer_sends import sequences_in
from gtm_core.sequencer_stats_read import PayloadError, load_stats_dir, sequence_rows

PROFILE = "democo"
SEC, CEO, CTO = "Sa1Bb2Cc3D", "Dd4Ee5Ff6G", "Gg7Hh8Ii9J"  # fictional, provider-shaped (10 alnum)
NEW = "Zz0Yy9Xx8W"

# prospects[0] keys of a real get_sequence_stats reply (values are digit strings there).
_PROSPECT_KEYS = (
    "total open replied clicked unsubscribed contacted notContacted completed active upcoming "
    "inActive waiting bounced paused finished uncategorized interested notInterested meetingBooked "
    "outOfOffice closed notNow doNotContact interestedDealValue meetingBookedDealValue "
    "closedDealValue sentNoOpen openedNoReply openedNoClicked clickedNoReply"
).split()
_STATUS_KEYS = (
    "delivered opened clicked replied failed blockBounced hardBounced softBounced bounced scheduled"
).split()


def stats_reply(sid, total, *, name="Fictional list one", contacted=0, unsub=0, status=None):
    """A real-shaped ``get_sequence_stats`` reply."""
    prospects = dict.fromkeys(_PROSPECT_KEYS, "0")
    prospects.update(total=str(total), contacted=str(contacted), unsubscribed=str(unsub))
    counts = dict.fromkeys(_STATUS_KEYS, 0)
    counts.update(status or {})
    return {
        "message": "Sequence stats fetched successfully",
        "payload": {
            "prospects": [prospects],
            "emails": {"total": sum(counts.values()), "status": counts},
            "sequenceName": name,
            "sequenceId": sid,
            "client": {},
        },
    }


def list_reply(*rows):
    """A ``list_sequences`` reply; each row is ``(id, title, active)``."""
    return {
        "message": "ok",
        "payload": [{"id": i, "title": t, "active": a, "steps": []} for i, t, a in rows],
    }


def staged(sid, enrolled, *, status="paused", wave=None):
    row = {
        "event": "sequence_staged",
        "skill": "email-sequence",
        "provider": "saleshandy",
        "sequence_id": sid,
        "touches": 3,
        "leads_total": 160,
        "leads_enrolled": enrolled,
        "status": status,
    }
    return {**row, "wave": wave} if wave else row


def ev(event, sid=None, **fields):
    row = {"event": event, "skill": "email-sequence", "provider": "saleshandy"}
    if sid:
        row["sequence_id"] = sid
    return {**row, **fields}


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A content root with one profile. ``put`` writes history, cells.toml, the list and stats."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    base = tmp_path / PROFILE
    (base / "prospects" / "sequences").mkdir(parents=True)
    (tmp_path / "stats").mkdir()

    class Home:
        root = tmp_path
        history = base / "history.jsonl"
        cells = base / "prospects" / "sequences" / "cells.toml"
        list_file = tmp_path / "list.json"
        stats_dir = tmp_path / "stats"

        def put(self, *, history=(), registered=(), live=(), stats=()):
            self.history.write_text("".join(json.dumps(r) + "\n" for r in history))
            self.cells.write_text(
                "".join(
                    f'[[sequence]]\nid = "{i}"\ncsv = "{i}-list.csv"\nspec = "spec-{i}.md"\n\n'
                    for i in registered
                )
            )
            self.list_file.write_text(json.dumps(list_reply(*live)))
            for old in self.stats_dir.glob("*.json"):
                old.unlink()
            for n, reply in enumerate(stats):
                (self.stats_dir / f"s{n}.json").write_text(json.dumps(reply))

        def run(self, *flags):
            return rec.main(
                [
                    "--profile",
                    PROFILE,
                    "--sequences",
                    str(self.list_file),
                    "--stats-dir",
                    str(self.stats_dir),
                    *flags,
                ]
            )

        def digest(self):
            return hashlib.sha256(self.history.read_bytes()).hexdigest()

    return Home()


def codes(capsys_or_json) -> list[tuple[str, str]]:
    return [(f["code"], f["sequence_id"]) for f in capsys_or_json["findings"]]


def run_json(home, capsys, *flags):
    code = home.run("--json", *flags)
    return code, json.loads(capsys.readouterr().out)


# ── the 2026-10-02 incident shape ─────────────────────────────────────────────────────────────

INCIDENT_LIVE = [(SEC, "Security list", True), (CEO, "CEO list", True), (CTO, "CTO list", True)]
INCIDENT_STATS = [
    stats_reply(SEC, 58, name="Security list"),
    stats_reply(CEO, 142, name="CEO list"),
    stats_reply(CTO, 35, name="CTO list"),
]
# What the ledger held that morning: the wave-A staging rows, and nothing for the 201-person load.
INCIDENT_HISTORY = [
    staged(SEC, 0),
    staged(CEO, 0),
    staged(CTO, 0),
    staged(SEC, 1, wave="A"),
    staged(CEO, 24, wave="A"),
    staged(CTO, 9, wave="A"),
]
# cells.toml held a placeholder per cell, never the real id.
INCIDENT_REGISTERED = ("seq-cell-a-20260926", "seq-cell-b-20260926")


def test_the_incident_shape_is_found(home, capsys):
    home.put(
        history=INCIDENT_HISTORY,
        registered=INCIDENT_REGISTERED,
        live=INCIDENT_LIVE,
        stats=INCIDENT_STATS,
    )
    code, out = run_json(home, capsys)
    assert code == 2
    found = codes(out)
    for sid, gap in ((SEC, 57), (CEO, 118), (CTO, 26)):
        assert ("prospects-without-load-event", sid) in found
        row = next(
            f
            for f in out["findings"]
            if f["code"] == "prospects-without-load-event" and f["sequence_id"] == sid
        )
        assert row["evidence"]["gap"] == gap
        assert ("active-without-activation-event", sid) in found
        assert ("sequence-unregistered", sid) in found
    placeholders = {s for c, s in found if c == "placeholder-id-registered"}
    assert placeholders == set(INCIDENT_REGISTERED)
    assert out["errors"] == 9 and out["warnings"] == 2


def test_once_recorded_and_registered_the_same_state_is_clean(home, capsys):
    """The ledger as it stood AFTER the backfill: the retroactive events are accounted for."""
    history = INCIDENT_HISTORY + [
        ev("sequence_loaded_unrecorded", SEC, prospects_loaded=57, recorded_retroactively=True),
        ev("sequence_loaded_unrecorded", CEO, prospects_loaded=118, recorded_retroactively=True),
        ev("sequence_loaded_unrecorded", CTO, prospects_loaded=26, recorded_retroactively=True),
        *(ev("sequence_activated_by_operator", s) for s in (SEC, CEO, CTO)),
        *(ev("sequence_paused_by_operator", s, active=False) for s in (SEC, CEO, CTO)),
    ]
    live = [(s, t, False) for s, t, _ in INCIDENT_LIVE]
    home.put(history=history, registered=(SEC, CEO, CTO), live=live, stats=INCIDENT_STATS)
    code, out = run_json(home, capsys)
    assert code == 0 and out["findings"] == []
    assert home.run() == 0 and "OK: every live sequence is registered" in capsys.readouterr().out


# ── the five named findings, one at a time ────────────────────────────────────────────────────


def test_active_without_activation_event_and_its_equivalents(home, capsys):
    home.put(registered=(SEC,), live=[(SEC, "L", True)], stats=[stats_reply(SEC, 0)])
    _, out = run_json(home, capsys)
    assert codes(out) == [("active-without-activation-event", SEC)]
    for equivalent in (
        [ev("sequence_activated_by_operator", SEC)],
        [ev("sequences_activated", sequences=[{"id": SEC, "title": "L"}])],
        [staged(SEC, 0, status="active")],
    ):
        home.put(
            history=equivalent,
            registered=(SEC,),
            live=[(SEC, "L", True)],
            stats=[stats_reply(SEC, 0)],
        )
        _, out = run_json(home, capsys)
        assert out["findings"] == [], equivalent


def test_reactivation_after_a_pause_is_as_unrecorded_as_none(home, capsys):
    history = [ev("sequence_activated_by_operator", SEC), ev("sequence_paused_by_operator", SEC)]
    home.put(
        history=history, registered=(SEC,), live=[(SEC, "L", True)], stats=[stats_reply(SEC, 0)]
    )
    _, out = run_json(home, capsys)
    (finding,) = out["findings"]
    assert finding["code"] == "active-without-activation-event"
    assert "last word on it is that it was paused" in finding["message"]


def test_inactive_without_pause_event_is_a_warning_and_never_fails_the_run(home, capsys):
    history = [ev("sequence_activated_by_operator", SEC)]
    home.put(
        history=history, registered=(SEC,), live=[(SEC, "L", False)], stats=[stats_reply(SEC, 0)]
    )
    code, out = run_json(home, capsys)
    assert code == 0 and codes(out) == [("inactive-without-pause-event", SEC)]
    assert out["findings"][0]["severity"] == "WARN"
    # A pause on record clears it; so does a batch pause row naming the id.
    for pause in (
        ev("sequence_paused_by_operator", SEC),
        ev("sequences_paused_and_contacted_suppressed", sequences=[{"id": SEC}]),
    ):
        home.put(
            history=[*history, pause],
            registered=(SEC,),
            live=[(SEC, "L", False)],
            stats=[stats_reply(SEC, 0)],
        )
        assert run_json(home, capsys)[1]["findings"] == []
    # A sequence that was never activated and is off is simply staged: nothing to say.
    home.put(registered=(SEC,), live=[(SEC, "L", False)], stats=[stats_reply(SEC, 0)])
    assert run_json(home, capsys)[1]["findings"] == []


def test_staged_is_a_snapshot_not_an_increment(home, capsys):
    """Staged at 4 and again at 9 is 9 people, not 13."""
    history = [staged(SEC, 4), staged(SEC, 9)]
    for held, expect in ((9, []), (10, [("prospects-without-load-event", SEC)])):
        home.put(
            history=history,
            registered=(SEC,),
            live=[(SEC, "L", False)],
            stats=[stats_reply(SEC, held)],
        )
        _, out = run_json(home, capsys)
        assert codes(out) == expect


def test_loads_that_are_one_event_each_are_summed(home, capsys):
    history = [
        staged(SEC, 24),
        ev("enrolled", SEC, leads_enrolled=40),
        ev("enrolled", SEC, lead_count=10),  # the dispatcher's own key
        ev("prospects_enrolled", SEC, enrolled="8"),  # a count typed as text, as the ledger has it
    ]
    assert index_history(history)[SEC].accounted == 24 + 40 + 10 + 8


def test_a_ledger_claiming_more_than_the_sequencer_holds_is_reported(home, capsys):
    """A staged snapshot AND an enrolled row for the same 24 people count twice; that must show."""
    history = [staged(SEC, 24), ev("enrolled", SEC, lead_count=24)]
    home.put(
        history=history, registered=(SEC,), live=[(SEC, "L", False)], stats=[stats_reply(SEC, 24)]
    )
    code, out = run_json(home, capsys)
    assert code == 0 and codes(out) == [("events-exceed-live-total", SEC)]


def test_unregistered_covers_a_half_written_cells_row(home, capsys):
    home.put(registered=(), live=[(SEC, "L", False)], stats=[stats_reply(SEC, 0)])
    home.cells.write_text(f'[[sequence]]\nid = "{SEC}"\n')  # id with no csv or spec
    _, out = run_json(home, capsys)
    (finding,) = out["findings"]
    assert (
        finding["code"] == "sequence-unregistered" and finding["evidence"]["in_cells_toml"] is True
    )
    assert "lacks a csv or spec" in finding["message"]


def test_placeholder_ids(home, capsys):
    registered = (SEC, "seq-cell-20260926", "DRAFT-ceo-20260926", NEW)
    home.put(registered=registered, live=[(SEC, "L", False)], stats=[stats_reply(SEC, 0)])
    _, out = run_json(home, capsys)
    by_id = {
        f["sequence_id"]: f for f in out["findings"] if f["code"] == "placeholder-id-registered"
    }
    # NEW is provider-shaped, so a stale registration of a since-deleted sequence is not a placeholder.
    assert set(by_id) == {"seq-cell-20260926", "DRAFT-ceo-20260926"}
    assert "never replaced" in by_id["seq-cell-20260926"]["message"]
    assert "draft id" in by_id["DRAFT-ceo-20260926"]["message"]
    # Real placeholders are listed before the expected drafts.
    ids = [f["sequence_id"] for f in out["findings"]]
    assert ids.index("seq-cell-20260926") < ids.index("DRAFT-ceo-20260926")


# ── the join is stable: the same id string everywhere, never fuzzy ────────────────────────────


def test_ids_are_compared_exactly_never_by_case(home, capsys):
    """A stats file for 'sa1bb2cc3d' must not be read as the stats of 'Sa1Bb2Cc3D'."""
    lower = SEC.lower()
    home.put(
        history=[
            ev("sequence_activated_by_operator", lower)
        ],  # the ledger names the lowercase twin
        registered=(SEC,),
        live=[(SEC, "L", True)],
        stats=[stats_reply(lower, 5)],
    )
    _, out = run_json(home, capsys)
    found = set(codes(out))
    assert ("stats-missing", SEC) in found  # its own stats are absent
    assert ("stats-without-listing", lower) in found  # the twin's are stray
    assert ("active-without-activation-event", SEC) in found  # the twin's activation is not its own
    assert ("prospects-without-load-event", SEC) not in found  # nothing was compared across the two


def test_a_whitespace_padded_id_is_refused_not_trimmed(home, capsys):
    home.put(registered=(SEC,), live=[(SEC, "L", False)], stats=[stats_reply(SEC + " ", 5)])
    assert home.run() == 3
    assert "no usable sequenceId" in capsys.readouterr().err


def test_the_strict_reader_and_the_sends_ledgers_reader_agree_on_the_real_shape():
    reply = stats_reply(SEC, 58)
    assert [r["sequenceId"] for r in sequence_rows(reply, "x")] == [SEC]
    assert [r["sequenceId"] for r in sequences_in(reply)] == [SEC]


def test_a_shape_the_sends_ledgers_reader_would_read_differently_is_refused(home, capsys):
    """The strict reader is cross-checked against ``sequences_in``; they must never disagree."""
    nested = {"payload": {"sequences": [stats_reply(SEC, 5)["payload"]]}}
    assert len(sequences_in(nested)) == 0  # the sends ledger would read nothing here
    with pytest.raises(PayloadError, match="two readers disagree"):
        sequence_rows(nested, "x")
    home.put(registered=(SEC,), live=[(SEC, "L", False)], stats=[nested])
    assert home.run() == 3 and "two readers disagree" in capsys.readouterr().err


def test_the_join_through_cells_toml_is_the_one_the_pipeline_uses(home):
    """Every id the reply-attribution join can use is one cells.toml names raw; never an invention."""
    home.put(registered=(SEC, "seq-x-20260926"))
    home.cells.write_text(home.cells.read_text() + '[[sequence]]\nid = "Half000000"\n')
    raw, joinable = rec.registered_ids(PROFILE, home.root)
    assert joinable == {r["sequence_id"] for r in load_cell_map(PROFILE, home.root)}
    assert joinable <= raw and "Half000000" in raw - joinable


# ── fail loudly rather than report a confident clean result ───────────────────────────────────


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda r: r["payload"][0].pop("active"), "no true/false `active`"),
        (lambda r: r["payload"][0].pop("id"), "no usable id"),
        (lambda r: r["payload"][0].update(active="yes"), "no true/false `active`"),
        (lambda r: r["payload"].append({**r["payload"][0]}), "twice"),
        (lambda r: r["payload"].clear(), "empty read is not a clean read"),
        (lambda r: r.pop("payload"), "expected {payload"),
    ],
)
def test_a_list_reply_lacking_what_the_join_needs_exits_3(home, capsys, mutate, message):
    home.put(registered=(SEC,), live=[(SEC, "L", False)], stats=[stats_reply(SEC, 0)])
    reply = json.loads(home.list_file.read_text())
    mutate(reply)
    home.list_file.write_text(json.dumps(reply))
    assert home.run() == 3
    assert message in capsys.readouterr().err


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda r: r["payload"].pop("sequenceId"), "no usable sequenceId"),
        (lambda r: r["payload"].pop("prospects"), "`prospects` is missing"),
        (lambda r: r["payload"]["prospects"][0].pop("total"), "prospects[0].total is missing"),
        (lambda r: r["payload"]["prospects"][0].update(total="n/a"), "a counter is a whole number"),
        (lambda r: r["payload"]["prospects"][0].update(total=True), "a counter is a whole number"),
    ],
)
def test_a_stats_reply_lacking_what_the_join_needs_exits_3(home, capsys, mutate, message):
    reply = stats_reply(SEC, 5)
    mutate(reply)
    home.put(registered=(SEC,), live=[(SEC, "L", False)], stats=[reply])
    assert home.run() == 3
    assert message in capsys.readouterr().err


def test_unreadable_inputs_exit_3(home, capsys):
    home.put(registered=(SEC,), live=[(SEC, "L", False)], stats=[stats_reply(SEC, 0)])
    home.history.write_text('{"event": "sequence_staged"}\nnot json\n')
    assert home.run() == 3 and "history.jsonl line 2" in capsys.readouterr().err
    home.history.write_text("[1, 2]\n")
    assert home.run() == 3 and "not a JSON object" in capsys.readouterr().err
    home.history.write_text("")
    home.cells.write_text("this is = not [toml")
    assert home.run() == 3 and "cells.toml is not readable TOML" in capsys.readouterr().err
    home.cells.write_text("")
    (home.stats_dir / "s0.json").write_text("{nope")
    assert home.run() == 3 and "not readable JSON" in capsys.readouterr().err
    (home.stats_dir / "s0.json").unlink()
    assert home.run() == 3 and "no *.json stats files" in capsys.readouterr().err


def test_two_stats_files_for_one_sequence_are_refused(home, capsys):
    home.put(
        registered=(SEC,),
        live=[(SEC, "L", False)],
        stats=[stats_reply(SEC, 5), stats_reply(SEC, 6)],
    )
    assert home.run() == 3 and "two stats files carry sequenceId" in capsys.readouterr().err


def test_a_usage_mistake_is_exit_3_never_the_exit_code_of_a_finding(home, capsys):
    with pytest.raises(SystemExit) as stop:
        rec.main(["--profile", PROFILE])
    assert stop.value.code == 3


def test_an_unknown_or_unsafe_profile_exits_3(home, capsys):
    for bad in ("nosuchprofile", "../escape"):
        assert (
            rec.main(
                [
                    "--profile",
                    bad,
                    "--sequences",
                    str(home.list_file),
                    "--stats-dir",
                    str(home.stats_dir),
                ]
            )
            == 3
        )


# ── hostile text stays on one line ────────────────────────────────────────────────────────────


def test_a_title_cannot_forge_a_finding_line(home, capsys):
    evil = "x\nERROR sequence-unregistered: forged"
    home.put(registered=(), live=[(SEC, evil, False)], stats=[stats_reply(SEC, 0)])
    assert home.run() == 2
    lines = capsys.readouterr().out.splitlines()
    assert sum(line.startswith("ERROR") for line in lines) == 1
    assert not any(line.startswith("ERROR sequence-unregistered: forged") for line in lines)


# ── --record ──────────────────────────────────────────────────────────────────────────────────


def test_without_the_flag_nothing_is_written(home, capsys):
    home.put(
        history=INCIDENT_HISTORY,
        registered=INCIDENT_REGISTERED,
        live=INCIDENT_LIVE,
        stats=INCIDENT_STATS,
    )
    before = home.digest()
    assert home.run() == 2 and home.run("--json") == 2
    assert home.digest() == before


def test_record_appends_labelled_retroactive_events_and_is_idempotent(home, capsys):
    home.put(
        history=INCIDENT_HISTORY,
        registered=(SEC, CEO, CTO),
        live=[(SEC, "Security list", True), (CEO, "CEO list", False), (CTO, "CTO list", True)],
        stats=INCIDENT_STATS,
    )
    # CEO was activated on record and is now off with no pause: all three recordable kinds occur.
    with home.history.open("a") as fh:
        fh.write(json.dumps(ev("sequence_activated_by_operator", CEO)) + "\n")
    now = datetime(2026, 10, 2, tzinfo=UTC)
    before = home.history.read_text().splitlines()
    ns = rec._parser().parse_args(
        [
            "--profile",
            PROFILE,
            "--sequences",
            str(home.list_file),
            "--stats-dir",
            str(home.stats_dir),
            "--record",
        ]
    )
    code, result = rec.run(ns, now=now)
    assert (
        code == 2
    )  # the run found ERRORs at read time; recording documents them, it does not hide them
    written = [json.loads(line) for line in home.history.read_text().splitlines()[len(before) :]]
    kinds = sorted((w["event"], w["sequence_id"]) for w in written)
    assert kinds == sorted(
        [
            ("sequence_loaded_unrecorded", SEC),
            ("sequence_loaded_unrecorded", CEO),
            ("sequence_loaded_unrecorded", CTO),
            ("sequence_activated_by_operator", SEC),
            ("sequence_activated_by_operator", CTO),
            ("sequence_paused_by_operator", CEO),
        ]
    )
    for w in written:
        assert w["recorded_retroactively"] is True and w["recorded_on"] == "2026-10-02"
        assert w["skill"] == "sequencer-reconcile" and "sequencer_reconcile" in w["evidence"]
        assert (
            w["occurred_at"] is None and w["prev_sha256"]
        )  # appended through Ledgers: hash-chained
    gaps = {
        w["sequence_id"]: w["prospects_loaded"]
        for w in written
        if w["event"] == "sequence_loaded_unrecorded"
    }
    assert gaps == {SEC: 57, CEO: 118, CTO: 26}
    assert (
        next(w for w in written if w["event"] == "sequence_paused_by_operator")["active"] is False
    )
    # A second run records nothing and finds nothing left to record.
    after = home.digest()
    code, out = run_json(home, capsys, "--record")
    assert out["recorded"] == [] and home.digest() == after and code == 0


def test_record_revalidates_each_finding_against_the_ledger_as_it_now_stands(home):
    home.put(history=[], registered=(SEC,), live=[(SEC, "L", True)], stats=[stats_reply(SEC, 0)])
    live = list_reply((SEC, "L", True))["payload"]
    stats = load_stats_dir(home.stats_dir, need_emails=False)
    raw, joinable = rec.registered_ids(PROFILE, home.root)
    findings = rec.reconcile(live, stats, [], raw, joinable)
    kwargs = {
        "profile": PROFILE,
        "content_root": home.root,
        "live": live,
        "stats": stats,
        "raw_ids": raw,
        "joinable": joinable,
        "evidence_source": "test",
    }
    first, _ = rec.record(findings, **kwargs)
    # The SAME stale findings again, with the ledger now holding the row.
    again, skipped = rec.record(findings, **kwargs)
    assert len(first) == 1 and again == [] and "already recorded" in skipped[0]
    assert sum(1 for _ in Path(home.history).read_text().splitlines()) == 1


def test_record_never_writes_unregistered_or_placeholder_findings(home, capsys):
    """Those are cells.toml's to fix through the registration flow, not the ledger's."""
    home.put(registered=("seq-x-1",), live=[(SEC, "L", False)], stats=[stats_reply(SEC, 0)])
    before = home.digest()
    code, out = run_json(home, capsys, "--record")
    assert code == 2 and out["recorded"] == [] and home.digest() == before
    assert ("sequence-unregistered", SEC) in codes(out)


# ── the two surfaces agree ────────────────────────────────────────────────────────────────────


def test_text_and_json_report_the_same_findings(home, capsys):
    home.put(
        history=INCIDENT_HISTORY,
        registered=INCIDENT_REGISTERED,
        live=INCIDENT_LIVE,
        stats=INCIDENT_STATS,
    )
    _, as_json = run_json(home, capsys)
    home.run()
    text = capsys.readouterr().out.splitlines()
    assert text[1:] == [f["line"] for f in as_json["findings"]]
    assert f"{as_json['errors']} error(s), {as_json['warnings']} warning(s)" in text[0]


def test_payload_error_is_the_only_input_exception():
    with pytest.raises(PayloadError):
        sequence_rows({"payload": {"sequenceName": "no id"}}, "x")
