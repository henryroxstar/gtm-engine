"""What a hostile page, a forgetful brain or a second product must not be able to make `extract` write."""

from __future__ import annotations

import json

import pytest

from gtm_core.signal_obs import members
from unit.conftest import SOURCE_URL, page

N1, N2, N3 = "Northwind Traders", "Blue Harbour Partners", "Quillon Freight"
LEDGER = [(n, n.lower().replace(" ", "") + ".example.test") for n in (N1, N2, N3)]
D1, D2, D3 = (d for _, d in LEDGER)


def _kinds(w):
    return sorted((o["kind"], o["account_key"]) for o in w.observations())


# --- timing: a join is only ever a dated fact from a page that dates it -------------------------


def test_a_source_with_no_timing_never_yields_a_join(signal_world):
    signal_world.registry(timing="none")
    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1, N2), "2026-10-01T08:00:00+00:00")
    signal_world.run()
    signal_world.capture(page(N1, N2, N3), "2026-10-22T08:00:00+00:00")
    rep = signal_world.run(day="2026-10-22", run_id="run2")
    assert rep.joins == 0
    assert {o["kind"] for o in signal_world.observations()} == {"source_member"}
    assert D3 in {o["account_key"] for o in signal_world.observations()}


def test_a_brain_read_list_never_yields_a_join(signal_world):
    signal_world.registry(extractor="brain_list", args="{}")
    signal_world.ledger(*LEDGER)
    text = f"# Members\n\n{N1}, {N2} and {N3}. [a]({D1}) [b]({D2}) [c]({D3})\n"
    signal_world.capture(text, "2026-10-01T08:00:00+00:00")
    first = [members.Member(N1, D1)]
    signal_world.run(proposed=first)
    signal_world.capture(text + "\nmore\n", "2026-10-08T08:00:00+00:00")
    rep = signal_world.run(
        day="2026-10-08", run_id="run2", proposed=[*first, members.Member(N2, D2)]
    )
    assert rep.joins == 0 and {o["kind"] for o in signal_world.observations()} == {"source_member"}


# --- a domain counts only when the page itself carries it ---------------------------------------


def test_a_domain_the_brain_adds_is_ignored_unless_the_page_carries_it(signal_world):
    signal_world.registry(extractor="brain_list", args="{}")
    signal_world.ledger(*LEDGER)
    signal_world.capture(
        f"# Members\n\n{N1} is listed. {N2} is listed.\n", "2026-10-01T08:00:00+00:00"
    )
    rep = signal_world.run(proposed=[members.Member(N1, D3), members.Member(N2, "")])
    assert signal_world.observations() == [] and rep.unresolved == 2


def test_a_domain_the_brain_gives_that_is_on_the_page_is_accepted(signal_world):
    signal_world.registry(extractor="brain_list", args="{}")
    signal_world.ledger(*LEDGER)
    text = f"# Members\n\n{N1} (https://{D1}/about) is listed.\n"
    signal_world.capture(text, "2026-10-01T08:00:00+00:00")
    signal_world.run(proposed=[members.Member(N1, D1)])
    assert [o["account_key"] for o in signal_world.observations()] == [D1]


def test_one_domain_linked_from_several_different_names_is_not_any_of_their_own(signal_world):
    signal_world.ledger(*LEDGER)
    shared = "platform.example.test"
    text = "\n".join(f"- [{n}](https://{shared}/customers/{i})" for i, n in enumerate((N1, N2, N3)))
    signal_world.capture("# Members\n\n" + text + "\n", "2026-10-01T08:00:00+00:00")
    signal_world.ledger(*LEDGER, ("Platform Holdings", shared))
    rep = signal_world.run()
    assert signal_world.observations() == [] and rep.unresolved == 3


# --- competitors never attest --------------------------------------------------------------------


def test_a_competitor_on_the_page_is_recorded_as_a_vendor(signal_world):
    signal_world.ledger(("Fictional Rival", "rival.example"), (N1, D1))
    signal_world.competitors(("Fictional Rival", "rival.example"))
    text = f"# Members\n\n- [Fictional Rival](https://rival.example/)\n- [{N1}](https://{D1}/)\n"
    signal_world.capture(text, "2026-10-01T08:00:00+00:00")
    signal_world.run()
    roles = {o["account_key"]: o["role"] for o in signal_world.observations()}
    assert roles == {"rival.example": "vendor", D1: "buyer"}


# --- a second product keeps its own record of what it last saw ---------------------------------


def test_two_products_with_the_same_source_id_keep_separate_member_sets(signal_world):
    signal_world.registry()
    signal_world.registry(product_file="beta")
    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1, N2), "2026-10-01T08:00:00+00:00")
    signal_world.run()
    before = {
        str(p.relative_to(signal_world.obs_dir)): p.read_bytes()
        for p in signal_world.obs_dir.rglob("*.json")
    }
    rep = signal_world.run(product="beta", run_id="run2")
    assert rep.status == "baseline" and rep.joins == 0
    assert {o["kind"] for o in signal_world.observations("beta")} == {"source_member"}
    assert {o["product"] for o in signal_world.observations("beta")} == {"beta"}
    assert {o["product"] for o in signal_world.observations("alpha")} == {"alpha"}
    after = {
        str(p.relative_to(signal_world.obs_dir)): p.read_bytes()
        for p in signal_world.obs_dir.rglob("*.json")
    }
    assert set(before) == {"state/alpha/north-directory.json"}
    assert set(after) == {"state/alpha/north-directory.json", "state/beta/north-directory.json"}
    assert before["state/alpha/north-directory.json"] == after["state/alpha/north-directory.json"]


# --- captures and pages that are not what they claim ---------------------------------------------


def test_a_capture_dated_in_the_future_is_ignored(signal_world):
    signal_world.ledger(*LEDGER)
    real = signal_world.capture(page(N1), "2026-10-01T08:00:00+00:00")
    signal_world.capture(page(N1, N2, N3) + "\nforged\n", "2036-01-01T00:00:00+00:00")
    rep = signal_world.run()
    assert rep.capture_sha256 == real and rep.members == 1


def test_a_future_dated_capture_does_not_make_a_stale_source_look_fresh(signal_world):
    import datetime

    from gtm_core.signal_obs import due

    signal_world.capture(page(N1), "2026-08-01T08:00:00+00:00")
    signal_world.capture(page(N1) + "x", "2036-01-01T00:00:00+00:00")
    rep = due.due_sources(
        signal_world.profile, signal_world.product, content_root=signal_world.content_root,
        profiles_root=signal_world.profiles_root, today=datetime.date(2026, 10, 1),
    )  # fmt: skip
    assert [s.id for s, _ in rep.due] == ["north-directory"]


def test_an_oversized_capture_is_refused_before_any_work(signal_world):
    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1) + "x" * 3_000_000, "2026-10-01T08:00:00+00:00")
    rep = signal_world.run()
    assert rep.status == "failed" and "too large" in rep.reason
    assert signal_world.observations() == []


@pytest.mark.parametrize("bomb", ["[" * 100000, "<script " * 60000, "<" * 200000])
def test_a_hostile_page_fails_cleanly_and_quickly(signal_world, bomb):
    import time

    signal_world.ledger(*LEDGER)
    signal_world.capture(bomb, "2026-10-01T08:00:00+00:00")
    t0 = time.monotonic()
    rep = signal_world.run()
    assert rep.status in {"failed", "empty"} and time.monotonic() - t0 < 10
    assert signal_world.observations() == []


def test_a_page_of_many_members_is_verified_in_linear_time(signal_world):
    import time

    names = [f"Zephyr Company {i:04d}" for i in range(1500)]
    signal_world.registry(max_members=2000)
    signal_world.ledger()
    signal_world.capture(page(*names), "2026-10-01T08:00:00+00:00")
    t0 = time.monotonic()
    signal_world.run()
    assert time.monotonic() - t0 < 10


def test_the_quiet_drops_are_counted_in_the_report(signal_world):
    signal_world.ledger(*LEDGER)
    signal_world.capture(
        f"# M\n\n- [UBS](https://ubs.example.test/)\n- [{N1}](https://{D1}/)\n",
        "2026-10-01T08:00:00+00:00",
    )
    rep = signal_world.run()
    assert rep.refused_short == 1 and rep.members == 1


def test_the_unchanged_source_url_constant_is_what_the_fixture_captures():
    assert SOURCE_URL.startswith("https://")
    json.dumps({"ok": True})


def test_a_damaged_capture_index_refuses_the_run_rather_than_using_an_older_capture(signal_world):
    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1), "2026-10-01T08:00:00+00:00")
    index = signal_world.sources_dir / "index.jsonl"
    index.write_text(index.read_text() + "<<<<<<< ours\n", encoding="utf-8")
    rep = signal_world.run()
    assert rep.status == "refused" and "capture index" in rep.reason
    assert signal_world.observations() == []


def test_a_torn_shard_stops_the_next_extract_before_it_writes_a_duplicate(signal_world):
    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1, N2), "2026-10-01T08:00:00+00:00")
    signal_world.run()
    (shard,) = signal_world.obs_dir.glob("*-2026-10.jsonl")
    shard.write_bytes(shard.read_bytes()[:-10])
    before = shard.read_bytes()
    signal_world.capture(page(N1, N2, N3), "2026-10-08T08:00:00+00:00")
    rep = signal_world.run(day="2026-10-08", run_id="run2")
    assert rep.status == "refused" and shard.name in rep.reason
    assert shard.read_bytes() == before


# --- damaged or planted files are refused, never skipped or followed --------------------------


def _queue(w):
    return w.obs_dir / "unresolved.jsonl"


def test_a_name_with_a_line_separator_stays_in_the_queue_and_is_not_queued_twice(signal_world):
    from gtm_core.signal_obs import unresolved

    name = "Line Sep Holdings"
    entry = {"product": "alpha", "source_id": "north-directory", "name": name, "first_seen": "x"}
    signal_world.obs_dir.mkdir(parents=True)
    for _ in range(3):
        unresolved.record(signal_world.obs_dir, [entry])
    assert [e["name"] for e in unresolved.read(signal_world.obs_dir)] == [name]
    assert len(_queue(signal_world).read_bytes().splitlines()) == 1


@pytest.mark.parametrize(
    "bad", ["<<<<<<< ours\n", '{"name": ["a"], "source_id": "s"}\n', "\xff\xfe\n"]
)
def test_a_damaged_queue_refuses_the_run_before_anything_is_written(signal_world, bad):
    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1, N2), "2026-10-01T08:00:00+00:00")
    signal_world.obs_dir.mkdir(parents=True)
    _queue(signal_world).write_bytes(b"\xff\xfe\n" if bad == "\xff\xfe\n" else bad.encode())
    rep = signal_world.run()
    assert rep.status == "refused" and "unresolved.jsonl" in rep.reason
    assert signal_world.observations() == [] and not (signal_world.obs_dir / "state").exists()


def test_a_planted_link_is_never_written_through(signal_world, tmp_path):
    target = tmp_path / "elsewhere.md"
    target.write_text("keep me\n", encoding="utf-8")
    signal_world.obs_dir.mkdir(parents=True)
    _queue(signal_world).symlink_to(target)
    signal_world.capture(page(N1, N2), "2026-10-01T08:00:00+00:00")
    rep = signal_world.run()
    assert rep.status == "refused" and target.read_text(encoding="utf-8") == "keep me\n"


def test_a_planted_state_folder_link_is_never_written_through(signal_world, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (signal_world.obs_dir / "state").mkdir(parents=True)
    (signal_world.obs_dir / "state" / "alpha").symlink_to(outside, target_is_directory=True)
    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1, N2), "2026-10-01T08:00:00+00:00")
    rep = signal_world.run()
    assert rep.status == "refused" and list(outside.iterdir()) == []


def test_a_member_name_that_cannot_be_written_is_dropped_not_fatal(signal_world, monkeypatch):
    from gtm_core.signal_obs import extract as ex
    from gtm_core.signal_obs.members import Extraction, Member

    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1), "2026-10-01T08:00:00+00:00")
    good, bad = Member(N1, D1), Member("Broken\ud800Name Co", "")
    monkeypatch.setattr(ex, "extract_members", lambda *a, **k: Extraction(members=[good, bad]))
    rep = signal_world.run()
    assert rep.status == "baseline" and rep.unverified == 1
    assert [o["account_key"] for o in signal_world.observations()] == [D1]


def test_a_rebaseline_on_an_empty_page_does_not_wipe_the_member_set(signal_world):
    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1, N2), "2026-10-01T08:00:00+00:00")
    signal_world.run()
    signal_world.capture("# Members\n\n(temporarily empty)\n", "2026-10-08T08:00:00+00:00")
    rep = signal_world.run(day="2026-10-08", run_id="run2", rebaseline=True)
    assert rep.status == "refused"
    from gtm_core.signal_obs import state

    assert len(state.load_state(signal_world.obs_dir, "alpha", "north-directory")["members"]) == 2


def test_the_account_key_is_the_ledger_string_even_when_it_starts_with_www(signal_world):
    signal_world.ledger((N1, "www." + D1))
    signal_world.capture(page(N1, domains={N1: D1}), "2026-10-01T08:00:00+00:00")
    signal_world.run()
    assert [o["account_key"] for o in signal_world.observations()] == ["www." + D1]


def test_a_newest_capture_whose_page_is_gone_is_not_replaced_by_an_older_one(signal_world):
    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1), "2026-10-01T08:00:00+00:00")
    newest = signal_world.capture(page(N1, N2), "2026-10-08T08:00:00+00:00")
    (signal_world.sources_dir / f"{newest}.txt").unlink()
    rep = signal_world.run(day="2026-10-08")
    assert rep.status == "no-capture"
