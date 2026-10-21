"""R1.5: an observation is a fact with an identity; readers collapse repeats and refuse the unsafe.

Reader-level rows live here (c, h, i); the extraction rows (a, b, d-g) are in the second half,
which drives ``extract.run_extract`` over a fictional tenant.
"""

from __future__ import annotations

import datetime
import json

import pytest

from gtm_core.signal_obs import observations as obs

pytestmark = pytest.mark.usefixtures("switch_open")

NOW = datetime.datetime(2026, 10, 1, 9, 0, tzinfo=datetime.UTC)


def _rec(**over):
    base = {
        "kind": "source_member",
        "product": "alpha",
        "source_id": "north-directory",
        "source_url": "https://members.example.test/list",
        "capture_sha256": "a" * 64,
        "account_key": "northwind.example.test",
        "observed": "2026-10-01",
        "observed_basis": "first_seen_in_capture",
        "role": "buyer",
        "premise_at_write": "",
        "writer": "amy.run1",
    }
    base.update(over)
    return obs.make_observation(**base)


def _write(tmp_path, *records, name="amy.run1-2026-10.jsonl"):
    d = tmp_path / "prospects" / "observations"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return p


def _read(tmp_path, product="alpha"):
    return obs.read_all(tmp_path / "prospects" / "observations", product=product)


# --- identity ---------------------------------------------------------------------------------


def test_a_membership_identity_ignores_the_day_and_the_capture():
    a = _rec(observed="2026-10-01", capture_sha256="a" * 64)
    b = _rec(observed="2026-11-09", capture_sha256="b" * 64, writer="bo.run9")
    assert a["obs_id"] == b["obs_id"]


def test_an_event_identity_includes_the_day():
    a = _rec(kind="job_post", observed="2026-10-01")
    b = _rec(kind="job_post", observed="2026-10-02")
    assert a["obs_id"] != b["obs_id"]


def test_identity_separates_account_source_kind_and_person():
    base = _rec()
    assert _rec(account_key="blueharbour.example.test")["obs_id"] != base["obs_id"]
    assert _rec(source_id="other-list")["obs_id"] != base["obs_id"]
    assert _rec(kind="job_post")["obs_id"] != base["obs_id"]
    assert _rec(person_key="p:jane")["obs_id"] != base["obs_id"]


def test_c_two_writers_who_first_saw_a_member_on_different_days_make_one_membership(tmp_path):
    late = _rec(observed="2026-10-05", writer="bo.run9")
    early = _rec(observed="2026-10-01", writer="amy.run1")
    _write(tmp_path, late, name="bo.run9-2026-10.jsonl")
    _write(tmp_path, early, name="amy.run1-2026-10.jsonl")
    got = _read(tmp_path)
    assert got.problems == []
    assert len(got.observations) == 1
    assert got.observations[0]["observed"] == "2026-10-01"


def test_h_a_retraction_removes_the_fact_and_supersedes_replaces_it(tmp_path):
    member = _rec()
    retract = _rec(kind="retracted", supersedes=member["obs_id"], observed="2026-10-03")
    other = _rec(kind="job_post", account_key="blueharbour.example.test", observed="2026-10-01")
    newer = _rec(
        kind="job_post",
        account_key="blueharbour.example.test",
        role="vendor",
        supersedes=other["obs_id"],
        observed="2026-10-02",
        writer="amy.run2",
    )
    _write(tmp_path, member, retract, other, newer)
    ids = {o["obs_id"] for o in _read(tmp_path).observations}
    assert member["obs_id"] not in ids and other["obs_id"] not in ids
    assert retract["obs_id"] not in ids  # a retraction is not itself a live fact
    assert newer["obs_id"] in ids


def test_i_every_written_line_carries_the_run_products_and_a_line_without_one_is_refused(tmp_path):
    with pytest.raises(obs.ObservationError, match="product"):
        _rec(product="")
    good = _rec()
    bare = {k: v for k, v in good.items() if k != "product"}
    _write(tmp_path, good, bare)
    got = _read(tmp_path)
    assert got.observations == []
    assert got.refused_shards == ["amy.run1-2026-10.jsonl"]
    assert "product" in got.problems[0]["why"]


def test_the_reader_keeps_only_the_run_products_observations(tmp_path):
    mine = _rec()
    theirs = _rec(product="beta", account_key="quillon.example.test")
    _write(tmp_path, mine, theirs)
    assert [o["product"] for o in _read(tmp_path, "alpha").observations] == ["alpha"]
    assert [o["product"] for o in _read(tmp_path, "beta").observations] == ["beta"]


def test_a_newer_schema_refuses_the_shard_and_says_to_upgrade(tmp_path):
    rec = {**_rec(), "schema": 2}
    _write(tmp_path, rec)
    got = _read(tmp_path)
    assert got.observations == [] and "pull and upgrade" in got.problems[0]["why"]


def test_an_unknown_kind_or_a_malformed_line_refuses_that_shard_only(tmp_path):
    _write(tmp_path, _rec(), name="amy.run1-2026-10.jsonl")
    p = _write(tmp_path, _rec(account_key="blueharbour.example.test"), name="bo.run9-2026-10.jsonl")
    p.write_text(p.read_text() + "{not json\n", encoding="utf-8")
    got = _read(tmp_path)
    assert len(got.observations) == 1
    assert got.refused_shards == ["bo.run9-2026-10.jsonl"]


def test_a_closed_kind_set_is_enforced_on_write():
    with pytest.raises(obs.ObservationError, match="kind"):
        _rec(kind="vibes")


def test_shard_names_follow_utc_across_a_month_boundary():
    before = datetime.datetime(2026, 10, 31, 23, 59, tzinfo=datetime.UTC)
    after = datetime.datetime(2026, 11, 1, 0, 1, tzinfo=datetime.UTC)
    assert obs.shard_name("amy.run1", before) == "amy.run1-2026-10.jsonl"
    assert obs.shard_name("amy.run1", after) == "amy.run1-2026-11.jsonl"
    local = datetime.datetime(
        2026, 11, 1, 6, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=8))
    )
    assert obs.shard_name("amy.run1", local) == "amy.run1-2026-10.jsonl"


def test_a_member_and_a_join_for_the_same_account_are_one_membership(tmp_path):
    """A stale writer's later join must not become a second fact about the same membership."""
    assert _rec(kind="source_join")["obs_id"] == _rec()["obs_id"]
    first = _rec(observed="2026-10-10", writer="amy.run1")
    later = _rec(kind="source_join", observed="2026-11-20", writer="bo.run9")
    _write(tmp_path, first, name="amy.run1-2026-10.jsonl")
    _write(tmp_path, later, name="bo.run9-2026-11.jsonl")
    (got,) = _read(tmp_path).observations
    assert (got["kind"], got["observed"]) == ("source_member", "2026-10-10")


def test_a_leave_note_is_its_own_event_not_the_membership():
    assert _rec(kind="source_leave_noted")["obs_id"] != _rec()["obs_id"]


_HOSTILE = [
    {"kind": ["source_member"]},
    {"role": {"a": 1}},
    {"account_key": 5},
    {"person_key": ["a"]},
    {"supersedes": ["a"]},
    {"source_id": 7},
    {"schema": True},
    {"schema": 1.0},
    {"observed": "20261001"},
    {"observed": "2026-1-1"},
    {"capture_sha256": 5},
    {"premise_at_write": None},
    {"writer": {"x": 1}},
    {"obs_id": ["a"]},
]


@pytest.mark.parametrize(
    "over", _HOSTILE, ids=lambda o: next(iter(o)) + "=" + repr(next(iter(o.values())))[:12]
)
def test_a_wrongly_typed_field_refuses_the_shard_and_never_raises(tmp_path, over):
    bad = {**_rec(), **over}
    _write(tmp_path, bad)
    got = _read(tmp_path)
    assert got.observations == [] and got.refused_shards == ["amy.run1-2026-10.jsonl"]


def test_a_hostile_deeply_nested_line_refuses_the_shard_and_never_raises(tmp_path):
    d = tmp_path / "prospects" / "observations"
    d.mkdir(parents=True)
    (d / "amy.run1-2026-10.jsonl").write_text("[" * 200000 + "\n", encoding="utf-8")
    got = _read(tmp_path)
    assert got.observations == [] and got.refused_shards == ["amy.run1-2026-10.jsonl"]


def test_an_append_onto_a_torn_tail_is_refused_and_writes_nothing(tmp_path):
    d = tmp_path / "prospects" / "observations"
    shard = _write(tmp_path, _rec())
    shard.write_bytes(shard.read_bytes() + b'{"schema": 1, "ki')
    before = shard.read_bytes()
    with pytest.raises(obs.ObservationError, match="repair"):
        obs.append(d, shard.name, [_rec(account_key="blueharbour.example.test")])
    assert shard.read_bytes() == before


# --- extraction rows (a, b, d-g): driven through extract.run_extract ---------------------------

N1, N2, N3, N4 = "Northwind Traders", "Blue Harbour Partners", "Quillon Freight", "Marrowgate Labs"
LEDGER = [(n, n.lower().replace(" ", "") + ".example.test") for n in (N1, N2, N3, N4)]


def _kinds(world):
    return sorted({(o["kind"], o["account_key"]) for o in world.observations()})


def test_d_the_first_extraction_is_a_baseline_with_members_and_no_joins(signal_world):
    from unit.conftest import page

    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1, N2), "2026-10-01T08:00:00+00:00")
    report = signal_world.run()
    got = signal_world.observations()
    assert report.status == "baseline" and report.joins == 0
    assert [o["kind"] for o in got] == ["source_member", "source_member"]
    assert {o["account_key"] for o in got} == {
        "northwindtraders.example.test",
        "blueharbourpartners.example.test",
    }
    assert all(o["product"] == "alpha" and o["observed"] == "2026-10-01" for o in got)
    assert all(o["writer"] == "amy.run1" for o in got)


def test_a_the_same_capture_extracted_twice_adds_no_lines(signal_world):
    from unit.conftest import page

    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1, N2), "2026-10-01T08:00:00+00:00")
    signal_world.run()
    before = sorted(o["obs_id"] for o in signal_world.observations())
    again = signal_world.run(run_id="run2")
    assert again.written == 0
    assert sorted(o["obs_id"] for o in signal_world.observations()) == before


def test_b_a_later_capture_with_the_same_members_adds_no_lines(signal_world):
    from unit.conftest import page

    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1, N2), "2026-10-01T08:00:00+00:00")
    signal_world.run()
    # Same members, different page furniture, three weeks later: a new capture sha.
    signal_world.capture(page(N1, N2) + "\nFooter text changed.\n", "2026-10-22T08:00:00+00:00")
    later = signal_world.run(day="2026-10-22", run_id="run2")
    assert later.written == 0 and later.joins == 0
    assert {o["kind"] for o in signal_world.observations()} == {"source_member"}


def test_a_new_name_on_a_later_capture_is_one_join_dated_by_that_capture(signal_world):
    from unit.conftest import page

    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1, N2), "2026-10-01T08:00:00+00:00")
    signal_world.run()
    signal_world.capture(page(N1, N2, N3), "2026-10-22T08:00:00+00:00")
    later = signal_world.run(day="2026-10-22", run_id="run2")
    joins = [o for o in signal_world.observations() if o["kind"] == "source_join"]
    assert later.status == "diff" and later.joins == 1
    assert [(j["account_key"], j["observed"]) for j in joins] == [
        ("quillonfreight.example.test", "2026-10-22")
    ]


@pytest.mark.parametrize("prior", ["empty", "failed"])
def test_e_a_previous_extraction_that_was_empty_or_failed_refuses_the_diff(signal_world, prior):
    from unit.conftest import page

    signal_world.ledger(*LEDGER)
    if prior == "empty":
        signal_world.capture("# Our members\n\nNothing listed yet.\n", "2026-10-01T08:00:00+00:00")
    else:
        signal_world.capture("", "2026-10-01T08:00:00+00:00")
    first = signal_world.run()
    assert first.status == prior and signal_world.observations() == []
    signal_world.capture(page(N1, N2, N3), "2026-10-22T08:00:00+00:00")
    second = signal_world.run(day="2026-10-22", run_id="run2")
    assert second.status == "refused" and prior in second.reason
    assert signal_world.observations() == []
    fixed = signal_world.run(day="2026-10-22", run_id="run3", rebaseline=True)
    assert fixed.status == "baseline" and fixed.joins == 0
    assert {o["kind"] for o in signal_world.observations()} == {"source_member"}


def test_f_a_member_set_that_shrinks_by_more_than_half_is_refused(signal_world):
    from unit.conftest import page

    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1, N2, N3, N4), "2026-10-01T08:00:00+00:00")
    signal_world.run()
    signal_world.capture(page(N1), "2026-10-22T08:00:00+00:00")
    later = signal_world.run(day="2026-10-22", run_id="run2")
    assert later.status == "refused" and "layout changed?" in later.reason
    assert later.written == 0
    # The refused page did not become the new baseline: the original four are still the state.
    signal_world.capture(page(N1, N2, N3, N4), "2026-10-29T08:00:00+00:00")
    assert signal_world.run(day="2026-10-29", run_id="run3").status == "diff"


def test_f_exactly_half_is_not_more_than_half(signal_world):
    from unit.conftest import page

    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1, N2, N3, N4), "2026-10-01T08:00:00+00:00")
    signal_world.run()
    signal_world.capture(page(N1, N2), "2026-10-22T08:00:00+00:00")
    assert signal_world.run(day="2026-10-22", run_id="run2").status == "diff"


def test_g_a_pruned_previous_capture_does_not_change_the_diff(signal_world):
    import json as _json

    from unit.conftest import page

    signal_world.ledger(*LEDGER)
    sha1 = signal_world.capture(page(N1, N2), "2026-10-01T08:00:00+00:00")
    signal_world.run()
    # Prune the first capture: its text and its index row are gone.
    (signal_world.sources_dir / f"{sha1}.txt").unlink()
    idx = signal_world.sources_dir / "index.jsonl"
    keep = [ln for ln in idx.read_text().splitlines() if _json.loads(ln)["sha256"] != sha1]
    idx.write_text("\n".join(keep) + ("\n" if keep else ""))
    signal_world.capture(page(N1, N2, N3), "2026-10-22T08:00:00+00:00")
    later = signal_world.run(day="2026-10-22", run_id="run2")
    assert later.status == "diff" and later.joins == 1


def test_a_page_over_max_members_is_refused_whole(signal_world):
    from unit.conftest import page

    signal_world.registry(max_members=2)
    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1, N2, N3), "2026-10-01T08:00:00+00:00")
    report = signal_world.run()
    assert report.status == "refused" and "max_members" in report.reason
    assert signal_world.observations() == []


def test_an_inert_source_is_not_extracted(signal_world):
    from unit.conftest import page

    signal_world.registry(precision="6/10")
    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1, N2), "2026-10-01T08:00:00+00:00")
    report = signal_world.run()
    assert report.status == "refused" and "precision" in report.reason
    assert signal_world.observations() == []


def test_a_vendor_source_stamps_its_members_as_vendors(signal_world):
    from unit.conftest import page

    signal_world.registry(role="vendor")
    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1), "2026-10-01T08:00:00+00:00")
    signal_world.run()
    assert [o["role"] for o in signal_world.observations()] == ["vendor"]


def test_no_capture_is_reported_and_writes_nothing(signal_world):
    report = signal_world.run()
    assert report.status == "no-capture" and signal_world.observations() == []


def test_a_missing_observer_id_refuses_before_any_write(signal_world):
    from unit.conftest import page

    (signal_world.content_root / "realshape" / "settings.json").unlink()
    signal_world.ledger(*LEDGER)
    signal_world.capture(page(N1), "2026-10-01T08:00:00+00:00")
    report = signal_world.run()
    assert report.status == "refused" and "observer_id" in report.reason
    assert not signal_world.obs_dir.exists() or not list(signal_world.obs_dir.glob("*.jsonl"))


def test_a_shard_or_writer_name_with_a_trailing_newline_is_refused(tmp_path):
    import pytest

    from gtm_core.signal_obs import observations as obs

    with pytest.raises(obs.ObservationError):
        obs.append(tmp_path, "amy.r1-2026-10.jsonl\n", [])
    assert obs._WRITER_RE.match("amy\n") is None
