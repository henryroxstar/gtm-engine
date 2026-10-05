"""R1.6: names that could not be matched go to a person as one question, and only `--apply` writes."""

from __future__ import annotations

import csv
import datetime
import io
import json

from gtm_core.signal_obs import review
from unit.conftest import page

NW = ("Northwind Traders", "northwind.example.test")
BH = ("Blue Harbour Partners", "blueharbour.example.test")
DAY = datetime.date(2026, 10, 2)
NOW = datetime.datetime(2026, 10, 2, 9, tzinfo=datetime.UTC)


def _seed_two_unmatched(w):
    w.ledger()
    w.capture(
        page(NW[0], BH[0], domains={NW[0]: NW[1], BH[0]: BH[1]}),
        "2026-10-01T08:00:00+00:00",
    )
    w.run()


def _pending(w, today=DAY):
    return review.pending(
        w.profile,
        w.product,
        content_root=w.content_root,
        profiles_root=w.profiles_root,
        today=today,
    )


def _rows(sheet: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(sheet)))


def _apply(w, sheet, *, apply=False):
    return review.apply_sheet(
        w.profile,
        sheet,
        product=w.product,
        content_root=w.content_root,
        profiles_root=w.profiles_root,
        today=DAY,
        now=NOW,
        run_id="rev1",
        apply=apply,
    )


def _decide(sheet, **by_name):
    rows = _rows(sheet)
    for r in rows:
        if r["name"] in by_name:
            r["choice"], r["domain"] = by_name[r["name"]]
    out = io.StringIO()
    wr = csv.DictWriter(out, fieldnames=list(rows[0]))
    wr.writeheader()
    wr.writerows(rows)
    return out.getvalue()


def test_the_sheet_asks_one_question_and_offers_four_choices(signal_world):
    _seed_two_unmatched(signal_world)
    sheet = review.render_sheet(_pending(signal_world))
    rows = _rows(sheet)
    assert {r["question"] for r in rows} == {"Which company is this?"}
    assert {r["name"] for r in rows} == {NW[0], BH[0]}
    assert all(r["suggested_domain"] for r in rows)
    choices = rows[0]["choices"]
    for c in ("confirm", "different", "not-a-prospect", "skip"):
        assert c in choices


def test_without_apply_nothing_is_written(signal_world):
    _seed_two_unmatched(signal_world)
    sheet = _decide(review.render_sheet(_pending(signal_world)), **{NW[0]: ("confirm", "")})
    before = {p: p.read_bytes() for p in signal_world.content_root.rglob("*") if p.is_file()}
    plan = _apply(signal_world, sheet, apply=False)
    assert [d.name for d in plan.decisions] == [NW[0]] and plan.written == 0
    assert {
        p: p.read_bytes() for p in signal_world.content_root.rglob("*") if p.is_file()
    } == before


def test_a_decision_becomes_an_operator_observation_and_the_next_extract_resolves_it(signal_world):
    _seed_two_unmatched(signal_world)
    sheet = _decide(review.render_sheet(_pending(signal_world)), **{NW[0]: ("confirm", "")})
    done = _apply(signal_world, sheet, apply=True)
    assert done.written == 1
    (o,) = signal_world.observations()
    assert (o["kind"], o["resolved_by"], o["account_key"], o["member_name"]) == (
        "source_member",
        "operator",
        NW[1],
        NW[0],
    )
    assert o["product"] == "alpha" and o["observed_basis"] == "operator_decision"
    again = signal_world.run(day="2026-10-02", run_id="run2")
    assert again.written == 0  # the operator's line already is the membership
    assert [e["name"] for e in _pending(signal_world)] == [BH[0]]


def test_a_different_domain_is_taken_as_given_and_a_bad_one_is_refused(signal_world):
    _seed_two_unmatched(signal_world)
    sheet = _decide(
        review.render_sheet(_pending(signal_world)), **{NW[0]: ("different", "nw.example.test")}
    )
    _apply(signal_world, sheet, apply=True)
    assert [o["account_key"] for o in signal_world.observations()] == ["nw.example.test"]
    bad = _decide(
        review.render_sheet(_pending(signal_world)), **{BH[0]: ("different", "not a domain")}
    )
    plan = _apply(signal_world, bad, apply=True)
    assert plan.written == 0 and "domain" in plan.errors[0]


def test_not_a_prospect_is_remembered_and_skip_is_not(signal_world):
    _seed_two_unmatched(signal_world)
    sheet = _decide(
        review.render_sheet(_pending(signal_world)),
        **{NW[0]: ("not-a-prospect", ""), BH[0]: ("skip", "")},
    )
    _apply(signal_world, sheet, apply=True)
    assert signal_world.observations() == []
    assert [e["name"] for e in _pending(signal_world)] == [BH[0]]
    signal_world.capture(page(NW[0], BH[0]) + "\nmore\n", "2026-10-09T08:00:00+00:00")
    signal_world.run(day="2026-10-09", run_id="run3")
    assert [e["name"] for e in _pending(signal_world)] == [
        BH[0]
    ]  # the dismissed name is not re-queued


def test_entries_older_than_ninety_days_leave_the_sheet_but_stay_in_the_file(signal_world):
    _seed_two_unmatched(signal_world)
    later = datetime.date(2027, 1, 15)
    assert _pending(signal_world, today=later) == []
    queue = signal_world.obs_dir / "unresolved.jsonl"
    assert len(queue.read_text().splitlines()) == 2


def test_a_review_sheet_is_capped_at_fifty_rows_oldest_first(signal_world):
    names = [f"Zephyr Company {i:03d}" for i in range(60)]
    signal_world.registry(max_members=100)
    signal_world.ledger()
    signal_world.capture(page(*names), "2026-10-01T08:00:00+00:00")
    signal_world.run()
    got = _pending(signal_world)
    assert len(got) == review.SHEET_CAP == 50
    assert [e["name"] for e in got] == sorted(names)[:50]


def test_a_domain_not_in_the_ledger_is_never_imported_by_review(signal_world):
    _seed_two_unmatched(signal_world)
    latest = signal_world.content_root / "realshape" / "prospects" / "latest.json"
    before = latest.read_bytes()
    sheet = _decide(review.render_sheet(_pending(signal_world)), **{NW[0]: ("confirm", "")})
    done = _apply(signal_world, sheet, apply=True)
    assert latest.read_bytes() == before
    assert any("import" in n for n in done.notes)


def test_another_products_queue_entries_are_not_on_this_products_sheet(signal_world):
    _seed_two_unmatched(signal_world)
    foreign = {
        "product": "beta",
        "source_id": "other",
        "name": "Quillon Freight",
        "first_seen": "2026-10-01",
        "candidate_domain": "",
        "reason": "no-domain",
    }
    with (signal_world.obs_dir / "unresolved.jsonl").open("a") as f:
        f.write(json.dumps(foreign) + "\n")
    assert {e["name"] for e in _pending(signal_world)} == {NW[0], BH[0]}


def test_a_name_that_starts_like_a_formula_cannot_run_in_a_spreadsheet(signal_world):
    name = '=HYPERLINK("https://x.example.test","Northwind Traders")'
    signal_world.ledger()
    signal_world.capture(
        f"# M\n\n- [{name}](https://hostile.example.test/)\n", "2026-10-01T08:00:00+00:00"
    )
    signal_world.run()
    sheet = review.render_sheet(_pending(signal_world))
    cells = [v for r in _rows(sheet) for v in r.values()]
    assert not any(c.startswith(("=", "+", "-", "@", "\t", "\r")) for c in cells), cells
    assert any(c.startswith("'=") for c in cells)


def test_a_formula_looking_name_still_matches_its_queue_entry_when_the_sheet_comes_back(
    signal_world,
):
    signal_world.ledger()
    signal_world.capture(
        "# M\n\n- [+Plus Holdings](https://plus.example.test/)\n", "2026-10-01T08:00:00+00:00"
    )
    signal_world.run()
    entries = _pending(signal_world)
    assert [e["name"] for e in entries] == ["+Plus Holdings"]
    sheet = _decide(review.render_sheet(entries), **{"'+Plus Holdings": ("not-a-prospect", "")})
    done = _apply(signal_world, sheet, apply=True)
    assert done.errors == [] and [d.choice for d in done.decisions] == ["not-a-prospect"]
    assert _pending(signal_world) == []


def test_a_row_for_a_name_that_was_never_on_the_sheet_is_refused(signal_world):
    _seed_two_unmatched(signal_world)
    sheet = review.render_sheet(_pending(signal_world))
    rows = _rows(sheet)
    forged = {
        **rows[0],
        "name": "Quillon Freight",
        "choice": "different",
        "domain": "evil.example.test",
    }
    out = io.StringIO()
    wr = csv.DictWriter(out, fieldnames=list(forged))
    wr.writeheader()
    wr.writerow(forged)
    done = _apply(signal_world, out.getvalue(), apply=True)
    assert done.written == 0 and done.errors and signal_world.observations() == []


def test_the_same_source_and_name_queued_by_two_products_stays_two_entries(signal_world):
    from gtm_core.signal_obs import unresolved

    entry = {"source_id": "north-directory", "name": "Quillon Freight", "first_seen": "2026-10-01"}
    signal_world.obs_dir.mkdir(parents=True, exist_ok=True)
    assert unresolved.record(signal_world.obs_dir, [{**entry, "product": "alpha"}]) == 1
    assert unresolved.record(signal_world.obs_dir, [{**entry, "product": "beta"}]) == 1
    assert unresolved.record(signal_world.obs_dir, [{**entry, "product": "beta"}]) == 0
    assert {e["product"] for e in unresolved.read(signal_world.obs_dir)} == {"alpha", "beta"}


def test_a_dismissal_for_one_product_does_not_hide_the_name_from_another(signal_world):
    from gtm_core.signal_obs import unresolved

    base = {"source_id": "north-directory", "name": "Quillon Freight", "first_seen": "2026-10-01"}
    signal_world.obs_dir.mkdir(parents=True, exist_ok=True)
    unresolved.record(
        signal_world.obs_dir, [{**base, "product": "alpha"}, {**base, "product": "beta"}]
    )
    unresolved.dismiss(signal_world.obs_dir, {**base, "product": "beta"})
    assert [e["name"] for e in _pending(signal_world)] == ["Quillon Freight"]


def test_a_decision_for_an_account_already_observed_still_clears_the_name(signal_world):
    bravo = ("Bravo Inc", "bravo.example.test")
    alias = "Bravo Incorporated Holdings"
    signal_world.ledger(bravo)
    signal_world.capture(
        page(bravo[0], alias, domains={bravo[0]: bravo[1]}), "2026-10-01T08:00:00+00:00"
    )
    signal_world.run()
    assert [e["name"] for e in _pending(signal_world)] == [alias]
    sheet = _decide(review.render_sheet(_pending(signal_world)), **{alias: ("different", bravo[1])})
    _apply(signal_world, sheet, apply=True)
    assert _pending(signal_world) == []
    signal_world.capture(page(bravo[0], alias, "Extra Co") + "\n", "2026-10-09T08:00:00+00:00")
    signal_world.run(day="2026-10-09", run_id="run3")
    assert alias not in [e["name"] for e in _pending(signal_world)]


def test_review_refuses_while_a_shard_cannot_be_read(signal_world):
    import pytest

    from gtm_core.signal_obs.observations import ObservationError

    _seed_two_unmatched(signal_world)
    signal_world.ledger(NW)
    signal_world.capture(page(NW[0], domains={NW[0]: NW[1]}), "2026-10-02T08:00:00+00:00")
    signal_world.run(day="2026-10-02", run_id="run2")
    (shard,) = signal_world.obs_dir.glob("*-2026-10.jsonl")
    shard.write_bytes(shard.read_bytes()[:-10])
    with pytest.raises(ObservationError, match="cannot be read"):
        _pending(signal_world)
    with pytest.raises(ObservationError, match="cannot be read"):
        _apply(signal_world, "question\n")


def test_a_name_that_starts_with_an_apostrophe_and_a_formula_lead_round_trips(signal_world):
    hostile = ["'=wedge Holdings", "'-dash Corp", "'plain Quote Ltd", "=sum Co"]
    signal_world.obs_dir.mkdir(parents=True)
    from gtm_core.signal_obs import unresolved

    unresolved.record(
        signal_world.obs_dir,
        [
            {
                "product": "alpha",
                "source_id": "north-directory",
                "name": n,
                "first_seen": "2026-10-01",
                "candidate_domain": "",
                "reason": "no-domain",
            }
            for n in hostile
        ],
    )
    sheet = review.render_sheet(_pending(signal_world))
    filled = _decide(sheet, **{_cell_of(n): ("not-a-prospect", "") for n in hostile})
    plan = _apply(signal_world, filled, apply=False)
    assert plan.errors == [] and sorted(d.name for d in plan.decisions) == sorted(hostile)


def _cell_of(name):
    return review._cell(name)


def test_a_short_or_oversized_csv_row_is_an_error_not_a_crash(signal_world):
    _seed_two_unmatched(signal_world)
    short = "question,source_id,name\nWhich company is this?,north-directory\n"
    assert _apply(signal_world, short).errors == []
    huge = "question,source_id,name,choice\nq,s," + "x" * 200_000 + ",skip\n"
    assert _apply(signal_world, huge).errors


def test_competitor_confirmed_by_operator_is_assigned_vendor_role(signal_world):
    _seed_two_unmatched(signal_world)
    comp_file = signal_world.profiles_root / signal_world.profile / "knowledge" / "competitors.toml"
    comp_file.parent.mkdir(parents=True, exist_ok=True)
    comp_file.write_text(
        '[[competitor]]\nname = "Northwind Traders"\ndomain = "northwind.example.test"\ntier = "direct"\nsummary = "Direct competitor"\n',
        encoding="utf-8",
    )
    sheet = review.render_sheet(_pending(signal_world))
    filled = _decide(sheet, **{NW[0]: ("confirm", "")})
    rep = _apply(signal_world, filled, apply=True)
    assert rep.written == 1
    shards = list(signal_world.obs_dir.glob("*.jsonl"))
    records = [
        json.loads(line)
        for s in shards
        for line in s.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    nw_obs = [
        r for r in records if r.get("kind") == "source_member" and r.get("account_key") == NW[1]
    ]
    assert len(nw_obs) == 1
    assert nw_obs[0]["role"] == "vendor"
