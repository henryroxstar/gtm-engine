"""Tests for gtm_core.prospects_state — the safe latest.json merge/snapshot writer."""

from __future__ import annotations

import json

import pytest

from gtm_core import prospects_state as ps


def _write_latest(root, profile, items):
    p = ps.latest_path(profile, content_root=root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"kind": "prospects", "profile": profile, "items": items}), encoding="utf-8"
    )
    return p


def test_merge_adds_new_and_keeps_existing(tmp_path):
    _write_latest(tmp_path, "acme", [{"id": "a", "company": "Alpha", "status": "new"}])
    summary = ps.upsert_latest(
        "acme",
        [{"id": "b", "company": "Beta", "status": "new"}],
        "run-2",
        content_root=tmp_path,
    )
    assert summary["added"] == 1
    assert summary["total"] == 2
    data = ps.load_latest("acme", content_root=tmp_path)
    companies = {i["company"] for i in data["items"]}
    assert companies == {"Alpha", "Beta"}


def test_merge_preserves_operator_status(tmp_path):
    # operator marked Alpha 'contacted' via the dashboard
    _write_latest(tmp_path, "acme", [{"id": "a", "company": "Alpha", "status": "contacted"}])
    # a later run re-emits Alpha with status 'new' — must NOT clobber the sticky edit
    ps.upsert_latest(
        "acme",
        [{"id": "a", "company": "Alpha", "status": "new", "score": 9}],
        "run-2",
        content_root=tmp_path,
    )
    data = ps.load_latest("acme", content_root=tmp_path)
    alpha = next(i for i in data["items"] if i["company"] == "Alpha")
    assert alpha["status"] == "contacted"  # sticky preserved
    assert alpha["score"] == 9  # non-sticky refreshed


def test_merge_can_never_shrink_the_cumulative_file(tmp_path):
    # The exact regression: 100 existing accounts, a run emits only 1.
    # Merge-only means the result is 101, never 1 — a full-file overwrite is
    # unreachable through this path.
    _write_latest(tmp_path, "acme", [{"id": str(n), "company": f"C{n}"} for n in range(100)])
    summary = ps.upsert_latest(
        "acme",
        [{"id": "x", "company": "OnlyOne"}],
        "single-run",
        content_root=tmp_path,
    )
    assert summary["total"] == 101
    assert summary["added"] == 1
    data = ps.load_latest("acme", content_root=tmp_path)
    assert len(data["items"]) == 101


def test_lossy_name_collision_never_drops_existing_account(tmp_path):
    # BUG 1: two DISTINCT existing accounts whose names normalize to the same
    # suffix-stripped key ("Acme Inc" / "Acme LLC" -> "acme") must both survive a
    # merge, each keeping its operator-set status. Pre-fix the second silently
    # overwrote the first before any merge ran.
    _write_latest(
        tmp_path,
        "acme",
        [
            {"id": "acme-inc", "company": "Acme Inc", "domain": "acme.com", "status": "qualified"},
            {
                "id": "acme-llc",
                "company": "Acme LLC",
                "domain": "acmellc.com",
                "status": "contacted",
            },
        ],
    )
    ps.upsert_latest(
        "acme",
        [{"id": "zenith", "company": "Zenith", "domain": "zenith.com", "status": "new"}],
        "run-2",
        content_root=tmp_path,
    )
    data = ps.load_latest("acme", content_root=tmp_path)
    by_company = {i["company"]: i for i in data["items"]}
    assert set(by_company) == {"Acme Inc", "Acme LLC", "Zenith"}
    assert by_company["Acme Inc"]["status"] == "qualified"  # not clobbered by Acme LLC
    assert by_company["Acme LLC"]["status"] == "contacted"


def test_non_ascii_company_is_kept_not_dropped(tmp_path):
    # BUG 3: a pure non-ASCII name normalizes to an empty legacy key; it must
    # still be retained (keyed by domain here), not silently dropped.
    _write_latest(tmp_path, "acme", [{"id": "old", "company": "Old Co", "status": "contacted"}])
    ps.upsert_latest(
        "acme",
        [
            {
                "id": "",
                "company": "山田商事株式会社",
                "domain": "yamada-shoji.example",
                "status": "new",
            }
        ],
        "run-2",
        content_root=tmp_path,
    )
    data = ps.load_latest("acme", content_root=tmp_path)
    companies = {i["company"] for i in data["items"]}
    assert companies == {"Old Co", "山田商事株式会社"}


def test_normal_merge_never_shrinks_and_allow_shrink_param_accepted(tmp_path):
    # The shrink tripwire (BUG 4): a normal merge can only grow or stay level, so
    # it never trips; the allow_shrink override must be a real, accepted keyword.
    _write_latest(tmp_path, "acme", [{"id": "a", "company": "Alpha"}])
    summary = ps.upsert_latest("acme", [], "run-empty", allow_shrink=False, content_root=tmp_path)
    assert summary["total"] == 1  # empty incoming set -> result still holds Alpha, no shrink


def test_wrong_shape_latest_file_raises(tmp_path):
    # A valid-JSON-but-wrong-shape file must fail loud, not be treated as empty.
    p = ps.latest_path("acme", content_root=tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([{"id": "x"}]), encoding="utf-8")  # top-level list, not an object
    with pytest.raises(ValueError):
        ps.load_latest("acme", content_root=tmp_path)


def test_corrupt_existing_file_raises_not_silently_empties(tmp_path):
    # A present-but-corrupt file must raise, not be treated as empty (which would
    # let the next merge "shrink" the cumulative file to just the new run).
    p = ps.latest_path("acme", content_root=tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ this is not valid json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        ps.upsert_latest("acme", [{"id": "x", "company": "New"}], "run", content_root=tmp_path)


def test_snapshot_taken_before_write(tmp_path):
    _write_latest(tmp_path, "acme", [{"id": "a", "company": "Alpha"}])
    ps.upsert_latest("acme", [{"id": "b", "company": "Beta"}], "run-2", content_root=tmp_path)
    snaps = list(
        (ps.latest_path("acme", content_root=tmp_path).parent / ps.SNAPSHOT_DIRNAME).glob(
            "latest-*.json"
        )
    )
    assert len(snaps) == 1
    snap_data = json.loads(snaps[0].read_text())
    # snapshot holds the PRE-write state
    assert len(snap_data["items"]) == 1


def test_restore_from_newest_snapshot(tmp_path):
    _write_latest(tmp_path, "acme", [{"id": str(n), "company": f"C{n}"} for n in range(10)])
    ps.snapshot("acme", content_root=tmp_path)  # snapshot the good 10-item state
    # now corrupt the live file
    _write_latest(tmp_path, "acme", [{"id": "x", "company": "Broken"}])
    ps.restore("acme", content_root=tmp_path)
    data = ps.load_latest("acme", content_root=tmp_path)
    assert len(data["items"]) == 10


def test_atomic_write_leaves_no_tmp(tmp_path):
    _write_latest(tmp_path, "acme", [{"id": "a", "company": "Alpha"}])
    ps.upsert_latest("acme", [{"id": "b", "company": "Beta"}], "run-2", content_root=tmp_path)
    leftover = list(ps.latest_path("acme", content_root=tmp_path).parent.glob(".latest-*.tmp"))
    assert leftover == []


# ── set_status: the verb the eval writeback needs, and why merge cannot be it ──


def test_merge_cannot_deliver_a_status_change_which_is_why_set_status_exists(tmp_path):
    """Documents the trap that shaped this design, so nobody 'simplifies' it back.

    `status` is a STICKY field: an existing account keeps its prior value and the incoming
    one is discarded. That rule is correct — it stops a routine prospect run from reverting
    an operator's hand-made edit. But it also means a merge can never DELIVER a status
    change, so pushing an eval disqualification through `upsert_latest` would report
    success and change nothing.
    """
    _write_latest(tmp_path, "acme", [{"domain": "alpha.example", "status": "contacted"}])
    ps.upsert_latest(
        "acme",
        [{"domain": "alpha.example", "status": "disqualified"}],
        source_run="run-1",
        content_root=tmp_path,
    )
    items = ps.load_latest("acme", content_root=tmp_path)["items"]
    assert items[0]["status"] == "contacted", (
        "merge started delivering status changes — if that is intentional, the eval "
        "writeback's set_status path needs rechecking, and so does every prospect run"
    )


def test_set_status_changes_the_status_and_stamps_provenance(tmp_path):
    _write_latest(tmp_path, "acme", [{"domain": "alpha.example", "status": "contacted"}])
    summary = ps.set_status(
        "acme",
        {"d:alpha.example": "disqualified"},
        reason="eval-disqualified",
        source="eval-2026-08-22",
        content_root=tmp_path,
    )
    assert summary["changed"] == 1
    item = ps.load_latest("acme", content_root=tmp_path)["items"][0]
    assert item["status"] == "disqualified"
    assert item["disqualified_reason"] == "eval-disqualified"
    assert item["disqualified_by"] == "eval-2026-08-22", (
        "without provenance an operator cannot tell which eval round removed an account, "
        "and cannot reverse it"
    )


def test_set_status_leaves_unnamed_accounts_alone(tmp_path):
    """The positive control: it must touch exactly the accounts it was given."""
    _write_latest(
        tmp_path,
        "acme",
        [
            {"domain": "alpha.example", "status": "contacted"},
            {"domain": "beta.example", "status": "contacted"},
        ],
    )
    ps.set_status("acme", {"d:alpha.example": "disqualified"}, content_root=tmp_path)
    items = {
        i["domain"]: i["status"] for i in ps.load_latest("acme", content_root=tmp_path)["items"]
    }
    assert items == {"alpha.example": "disqualified", "beta.example": "contacted"}


def test_set_status_never_invents_an_account(tmp_path):
    """An unmatched key is reported, never created. Fabricating an account from a
    writeback is the same data-invention risk in the other direction."""
    _write_latest(tmp_path, "acme", [{"domain": "alpha.example", "status": "new"}])
    summary = ps.set_status("acme", {"d:ghost.example": "disqualified"}, content_root=tmp_path)
    assert summary["changed"] == 0
    assert summary["unmatched"] == ["d:ghost.example"]
    assert len(ps.load_latest("acme", content_root=tmp_path)["items"]) == 1


def test_set_status_can_never_change_the_item_count(tmp_path):
    _write_latest(
        tmp_path, "acme", [{"domain": f"c{i}.example", "status": "new"} for i in range(5)]
    )
    ps.set_status("acme", {"d:c0.example": "disqualified"}, content_root=tmp_path)
    assert len(ps.load_latest("acme", content_root=tmp_path)["items"]) == 5


def test_set_status_is_idempotent_and_skips_the_write_when_nothing_changes(tmp_path):
    _write_latest(tmp_path, "acme", [{"domain": "alpha.example", "status": "disqualified"}])
    summary = ps.set_status("acme", {"d:alpha.example": "disqualified"}, content_root=tmp_path)
    assert (summary["changed"], summary["unchanged"]) == (0, 1)
    assert summary["snapshot"] is None, "a no-op write still took a snapshot"


def test_set_status_snapshots_before_it_writes(tmp_path):
    _write_latest(tmp_path, "acme", [{"domain": "alpha.example", "status": "new"}])
    summary = ps.set_status("acme", {"d:alpha.example": "disqualified"}, content_root=tmp_path)
    assert summary["snapshot"], "no snapshot — a bad writeback would be unrecoverable"
    snaps = list(
        (ps.latest_path("acme", content_root=tmp_path).parent / ".snapshots").glob("*.json")
    )
    assert snaps


# --- multi-key identity index (B1/B2) --------------------------------------- #
# The merge indexed each existing item under ONE key, so an account first seen
# without a domain and later re-emitted WITH one hashed to a different bucket,
# missed the lookup, and was appended as a second row. The operator's status
# stranded on the orphan, and the shrink tripwire could never catch it because
# the file only ever grew.


def test_merge_finds_existing_row_when_domain_gained(tmp_path):
    _write_latest(tmp_path, "acme", [{"id": "x", "company": "Alpha", "status": "contacted"}])
    summary = ps.upsert_latest(
        "acme",
        [{"id": "x", "company": "Alpha", "domain": "alpha.example", "score": 7}],
        "run-2",
        content_root=tmp_path,
    )
    assert (summary["added"], summary["updated"]) == (0, 1)
    items = ps.load_latest("acme", content_root=tmp_path)["items"]
    assert len(items) == 1, "the enriched row was appended instead of merged"
    assert items[0]["status"] == "contacted", "sticky status stranded on an orphan row"
    assert items[0]["domain"] == "alpha.example"


def test_merge_finds_existing_row_by_company_when_id_lost(tmp_path):
    _write_latest(tmp_path, "acme", [{"company": "Alpha", "status": "contacted"}])
    summary = ps.upsert_latest(
        "acme",
        [{"company": "Alpha", "id": "x", "domain": "alpha.example"}],
        "run-2",
        content_root=tmp_path,
    )
    assert (summary["added"], summary["updated"]) == (0, 1)
    assert ps.load_latest("acme", content_root=tmp_path)["items"][0]["status"] == "contacted"


def test_merged_row_is_reachable_under_its_new_key_next_run(tmp_path):
    """Re-indexing after a merge, or the third run appends what the second merged."""
    _write_latest(tmp_path, "acme", [{"id": "x", "company": "Alpha"}])
    ps.upsert_latest(
        "acme",
        [{"id": "x", "company": "Alpha", "domain": "alpha.example"}],
        "r2",
        content_root=tmp_path,
    )
    summary = ps.upsert_latest(
        "acme", [{"domain": "alpha.example", "company": "Alpha"}], "r3", content_root=tmp_path
    )
    assert (summary["added"], summary["updated"]) == (0, 1)
    assert len(ps.load_latest("acme", content_root=tmp_path)["items"]) == 1


def test_keyless_items_are_appended_but_counted_separately(tmp_path):
    """A truly un-keyable row still appends — but it is reported, not silent."""
    _write_latest(tmp_path, "acme", [])
    summary = ps.upsert_latest(
        "acme", [{"notes": "no name, no domain, no id"}], "r1", content_root=tmp_path
    )
    assert summary["added"] == 1
    assert summary["keyless_appended"] == 1, "an un-keyable append must be visible in the summary"


def test_keyed_items_do_not_count_as_keyless(tmp_path):
    _write_latest(tmp_path, "acme", [])
    summary = ps.upsert_latest("acme", [{"domain": "alpha.example"}], "r1", content_root=tmp_path)
    assert summary["keyless_appended"] == 0


# --- set_status bookkeeping (B13) ------------------------------------------- #


def test_set_status_counts_one_account_once_despite_duplicate_positions(tmp_path):
    """A pre-existing duplicate key must not report two changes for one account."""
    _write_latest(
        tmp_path,
        "acme",
        [
            {"domain": "alpha.example", "status": "new"},
            {"domain": "alpha.example", "status": "new"},
        ],
    )
    summary = ps.set_status("acme", {"d:alpha.example": "disqualified"}, content_root=tmp_path)
    assert summary["changed"] == 1, "one account, one change — positions are not accounts"
    items = ps.load_latest("acme", content_root=tmp_path)["items"]
    assert all(i["status"] == "disqualified" for i in items), "every position must still be written"


def test_set_status_refreshes_generated_at(tmp_path):
    p = ps.latest_path("acme", content_root=tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "kind": "prospects",
                "profile": "acme",
                "generated_at": "2020-01-01T00:00:00+00:00",
                "items": [{"domain": "alpha.example", "status": "new"}],
            }
        ),
        encoding="utf-8",
    )
    ps.set_status("acme", {"d:alpha.example": "disqualified"}, content_root=tmp_path)
    data = ps.load_latest("acme", content_root=tmp_path)
    assert data["generated_at"] != "2020-01-01T00:00:00+00:00", "the file's timestamp lies"
