"""Boundaries and labels that an earlier suite left unpinned: each test fails if the line it names changes."""

from __future__ import annotations

import datetime
import json

import pytest

from gtm_core.signal_obs import observations as obs
from gtm_core.signal_obs import review, state
from unit.conftest import page

pytestmark = pytest.mark.usefixtures("switch_open")

N1, N2, N3 = "Northwind Traders", "Blue Harbour Partners", "Quillon Freight"
LEDGER = [(n, n.lower().replace(" ", "") + ".example.test") for n in (N1, N2, N3)]
D1, D2, D3 = (d for _, d in LEDGER)


def _kinds(w):
    return {o["account_key"]: o["kind"] for o in w.observations()}


def test_an_observation_is_dated_by_the_capture_not_by_the_day_extract_ran(signal_world):
    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1), "2026-10-01T08:00:00+00:00")
    signal_world.run(day="2026-10-05")
    (o,) = signal_world.observations()
    assert o["observed"] == "2026-10-01"


def test_a_name_queued_from_an_old_capture_is_dated_by_that_capture(signal_world):
    signal_world.capture(page(N1), "2026-10-01T08:00:00+00:00")
    signal_world.run(day="2026-10-05")
    (e,) = review.unresolved.read(signal_world.obs_dir)
    assert e["first_seen"] == "2026-10-01"


def test_a_member_that_resolves_later_is_a_member_and_only_a_new_name_is_a_join(signal_world):
    signal_world.ledger((N1, D1))
    signal_world.capture(page(N1, N2, domains={N2: D2}), "2026-10-01T08:00:00+00:00")
    signal_world.run()
    signal_world.ledger((N1, D1), (N2, D2), (N3, D3))
    signal_world.capture(
        page(N1, N2, N3, domains={N2: D2, N3: D3}) + "\nchanged\n", "2026-10-08T08:00:00+00:00"
    )
    rep = signal_world.run(day="2026-10-08", run_id="run2")
    assert _kinds(signal_world) == {D1: "source_member", D2: "source_member", D3: "source_join"}
    assert rep.joins == 1


def test_a_join_needs_the_previous_member_set_not_just_a_changed_page(signal_world):
    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1, N2), "2026-10-01T08:00:00+00:00")
    signal_world.run()
    signal_world.capture(page(N1, N2) + "\nreworded\n", "2026-10-08T08:00:00+00:00")
    signal_world.run(day="2026-10-08", run_id="run2")
    assert set(_kinds(signal_world).values()) == {"source_member"}


@pytest.mark.parametrize("lead", ["=", "+", "-", "@", "\t", "\r"])
def test_every_formula_lead_is_neutralised_and_comes_back(lead):
    name = f"{lead}Acme Labs"
    assert review._cell(name).startswith("'") and review._uncell(review._cell(name)) == name


def test_a_sheet_keeps_a_name_for_ninety_days_and_not_a_day_longer(signal_world):
    signal_world.capture(page(N1), "2026-10-01T08:00:00+00:00")
    signal_world.run()

    def names(today):
        return [
            e["name"]
            for e in review.pending(
                signal_world.profile,
                signal_world.product,
                content_root=signal_world.content_root,
                profiles_root=signal_world.profiles_root,
                today=today,
            )
        ]

    first = datetime.date(2026, 10, 1)
    assert names(first + datetime.timedelta(days=90)) == [N1]
    assert names(first + datetime.timedelta(days=91)) == []


def test_one_bad_row_stops_the_whole_sheet_from_being_applied(signal_world):
    signal_world.ledger()
    signal_world.capture(page(N1, N2, domains={N1: D1, N2: D2}), "2026-10-01T08:00:00+00:00")
    signal_world.run()
    sheet = (
        "question,source_id,name,choice,domain\n"
        f"q,north-directory,{N1},confirm,\n"
        "q,north-directory,Nobody Listed Ltd,confirm,\n"
    )
    done = review.apply_sheet(
        signal_world.profile,
        sheet,
        product=signal_world.product,
        content_root=signal_world.content_root,
        profiles_root=signal_world.profiles_root,
        today=datetime.date(2026, 10, 2),
        run_id="r",
        apply=True,
    )
    assert done.written == 0 and len(done.errors) == 1 and signal_world.observations() == []


def test_a_domain_a_firmographics_step_supplied_is_labelled_a_candidate(signal_world):
    signal_world.registry(extractor="heading_list", args="{ level = 2 }")
    signal_world.ledger(*LEDGER)
    signal_world.capture(f"# Members\n\n## {N1}\n", "2026-10-01T08:00:00+00:00")
    signal_world.run(candidates={N1: D1})
    (o,) = signal_world.observations()
    assert (o["account_key"], o["resolved_by"]) == (D1, "candidate")


def test_the_domain_the_page_links_beats_one_a_candidate_step_supplied(signal_world):
    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1, domains={N1: D1}), "2026-10-01T08:00:00+00:00")
    signal_world.run(candidates={N1: D2})
    (o,) = signal_world.observations()
    assert (o["account_key"], o["resolved_by"]) == (D1, "page")


def test_a_line_whose_obs_id_is_not_the_hash_of_its_fields_is_refused(tmp_path):
    rec = obs.make_observation(
        kind="source_member", product="alpha", source_id="s", source_url="https://x.example.test/",
        capture_sha256="a" * 64, account_key="a.example.test", observed="2026-10-01",
        observed_basis="first_seen_in_capture", role="buyer", premise_at_write="", writer="amy.r1",
    )  # fmt: skip
    rec["obs_id"] = "0" * 64
    d = tmp_path / "obs"
    d.mkdir()
    (d / "amy.r1-2026-10.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    got = obs.read_all(d, product="alpha")
    assert got.observations == [] and got.refused_shards == ["amy.r1-2026-10.jsonl"]


def test_append_refuses_a_record_that_is_not_valid_and_a_shard_name_that_is_not_a_shard(tmp_path):
    with pytest.raises(obs.ObservationError):
        obs.append(tmp_path, "amy.r1-2026-10.jsonl", [{"kind": "source_member"}])
    with pytest.raises(obs.ObservationError):
        obs.append(tmp_path, "notes.txt", [])
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "body",
    [
        {"schema": 2, "status": "ok", "members": {}},
        {"schema": 1, "status": "weird", "members": {}},
        {"schema": 1, "status": "ok", "members": {"a": 5}},
        {"schema": 1, "status": "ok", "members": []},
    ],
)
def test_a_member_set_file_with_the_wrong_schema_status_or_shape_is_refused(tmp_path, body):
    path = state.state_path(tmp_path, "alpha", "s")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(state.StateError):
        state.load_state(tmp_path, "alpha", "s")


def test_a_source_is_due_on_the_day_its_cadence_runs_out_and_not_a_day_before(signal_world):
    from gtm_core.signal_obs import due

    signal_world.capture(page(N1), "2026-09-01T08:00:00+00:00")

    def due_ids(today):
        rep = due.due_sources(
            signal_world.profile,
            signal_world.product,
            content_root=signal_world.content_root,
            profiles_root=signal_world.profiles_root,
            today=today,
        )
        return [s.id for s, _ in rep.due]

    assert due_ids(datetime.date(2026, 9, 30)) == []  # 29 days old, cadence 30
    assert due_ids(datetime.date(2026, 10, 1)) == ["north-directory"]  # 30 days old
