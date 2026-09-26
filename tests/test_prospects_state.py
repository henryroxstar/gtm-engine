"""Tests for gtm_core.prospects_state — the safe latest.json merge/snapshot writer."""

from __future__ import annotations

import csv
import json

import pytest

from gtm_core import prospects_state as ps
from gtm_core.prospects_consolidate.paths import ready_to_load_path


def _write_latest(root, profile, items):
    p = ps.latest_path(profile, content_root=root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"kind": "prospects", "profile": profile, "items": items}), encoding="utf-8"
    )
    return p


_POOL_COLUMNS = ("email", "company", "company_domain", "account_id")


def _write_ready_to_load(root, profile, rows):
    """A trimmed ``ready-to-load.csv`` — only the columns :func:`mark_replied` joins on."""
    p = ready_to_load_path(profile, content_root=root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_POOL_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in _POOL_COLUMNS})
    return p


def _write_master_list(root, profile, rows):
    from gtm_core.prospects_consolidate.paths import _pool_dir

    p = _pool_dir(profile, content_root=root) / "master-list.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_POOL_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in _POOL_COLUMNS})
    return p


def _write_cells_toml(root, profile, sequences):
    from gtm_core.prospects_consolidate.paths import _sequences_dir

    seq_dir = _sequences_dir(profile, content_root=root)
    seq_dir.mkdir(parents=True, exist_ok=True)
    cells = []
    for s in sequences:
        cells.append(
            f'[[sequence]]\nid = "{s["id"]}"\ncsv = "{s["csv"]}"\nspec = "{s.get("spec", "spec.md")}"\n'
        )
    (seq_dir / "cells.toml").write_text("\n".join(cells), encoding="utf-8")


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


# --- PS6: LEDGER_STATUSES validation + disqualified-only provenance fields --- #


def test_set_status_rejects_a_status_outside_the_ledger_vocabulary(tmp_path):
    _write_latest(tmp_path, "acme", [{"domain": "alpha.example", "status": "new"}])
    with pytest.raises(ValueError):
        ps.set_status("acme", {"d:alpha.example": "qualified"}, content_root=tmp_path)
    # Refused before any write — the file must be untouched.
    assert ps.load_latest("acme", content_root=tmp_path)["items"][0]["status"] == "new"


def test_disqualified_fields_are_only_stamped_for_the_disqualified_status(tmp_path):
    """A `replied` write must not carry `disqualified_reason`/`disqualified_by` — that
    would misleadingly imply the account was disqualified when it was not."""
    _write_latest(tmp_path, "acme", [{"domain": "alpha.example", "status": "new"}])
    ps.set_status(
        "acme",
        {"d:alpha.example": "replied"},
        reason="some-reason",
        source="some-source",
        content_root=tmp_path,
    )
    item = ps.load_latest("acme", content_root=tmp_path)["items"][0]
    assert item["status"] == "replied"
    assert "disqualified_reason" not in item
    assert "disqualified_by" not in item


def test_disqualified_fields_still_stamped_for_disqualified(tmp_path):
    """Regression guard: existing eval_writeback/lanes.decisions callers must still work."""
    _write_latest(tmp_path, "acme", [{"domain": "alpha.example", "status": "new"}])
    ps.set_status(
        "acme",
        {"d:alpha.example": "disqualified"},
        reason="eval-disqualified",
        source="eval-2026-09-10",
        content_root=tmp_path,
    )
    item = ps.load_latest("acme", content_root=tmp_path)["items"][0]
    assert item["disqualified_reason"] == "eval-disqualified"
    assert item["disqualified_by"] == "eval-2026-09-10"


# --- PS6: mark_replied — join a reply's email to its account, write `replied` ----- #


def test_mark_replied_matches_by_domain_and_writes_replied(tmp_path):
    _write_latest(
        tmp_path, "acme", [{"domain": "alpha.example", "company": "Alpha", "status": "new"}]
    )
    _write_ready_to_load(
        tmp_path,
        "acme",
        [{"email": "dana@alpha.example", "company": "Alpha", "company_domain": "alpha.example"}],
    )
    summary = ps.mark_replied("acme", ["dana@alpha.example"], source="test", content_root=tmp_path)
    assert summary["matched"] == ["dana@alpha.example"]
    assert summary["changed"] == 1
    assert summary["unmatched"] == []
    item = ps.load_latest("acme", content_root=tmp_path)["items"][0]
    assert item["status"] == "replied"


def test_mark_replied_matches_by_company_when_no_domain(tmp_path):
    _write_latest(tmp_path, "acme", [{"company": "Beta Co", "status": "new"}])
    _write_ready_to_load(tmp_path, "acme", [{"email": "sam@beta.example", "company": "Beta Co"}])
    summary = ps.mark_replied("acme", ["sam@beta.example"], source="test", content_root=tmp_path)
    assert summary["matched"] == ["sam@beta.example"]
    assert ps.load_latest("acme", content_root=tmp_path)["items"][0]["status"] == "replied"


def test_mark_replied_matches_by_stamped_account_id(tmp_path):
    """A row that only carries the stamped account_id (no domain/company overlap with the
    ledger row's own casing/spacing) must still resolve — the same fallback
    `_account_key_of` relies on elsewhere."""
    _write_latest(
        tmp_path, "acme", [{"account_id": "a-deadbeef01", "company": "", "status": "new"}]
    )
    _write_ready_to_load(
        tmp_path, "acme", [{"email": "rep@gamma.example", "account_id": "a-deadbeef01"}]
    )
    summary = ps.mark_replied("acme", ["rep@gamma.example"], source="test", content_root=tmp_path)
    assert summary["matched"] == ["rep@gamma.example"]
    assert ps.load_latest("acme", content_root=tmp_path)["items"][0]["status"] == "replied"


def test_mark_replied_never_overwrites_a_retired_status(tmp_path):
    """A positive reply must not un-disqualify (or otherwise revive) an account already
    retired — disqualified, do-not-contact, or closed-lost."""
    _write_latest(
        tmp_path,
        "acme",
        [{"domain": "alpha.example", "company": "Alpha", "status": "disqualified"}],
    )
    _write_ready_to_load(
        tmp_path,
        "acme",
        [{"email": "dana@alpha.example", "company": "Alpha", "company_domain": "alpha.example"}],
    )
    summary = ps.mark_replied("acme", ["dana@alpha.example"], source="test", content_root=tmp_path)
    assert summary["retired_skipped"] == ["dana@alpha.example"]
    assert summary["matched"] == []
    item = ps.load_latest("acme", content_root=tmp_path)["items"][0]
    assert item["status"] == "disqualified", "a reply must never revive a retired account"


@pytest.mark.parametrize("retired_status", ["disqualified", "do-not-contact", "closed-lost"])
def test_mark_replied_skips_every_retired_status(tmp_path, retired_status):
    _write_latest(
        tmp_path,
        "acme",
        [{"domain": "alpha.example", "company": "Alpha", "status": retired_status}],
    )
    _write_ready_to_load(
        tmp_path,
        "acme",
        [{"email": "dana@alpha.example", "company": "Alpha", "company_domain": "alpha.example"}],
    )
    summary = ps.mark_replied("acme", ["dana@alpha.example"], source="test", content_root=tmp_path)
    assert summary["retired_skipped"] == ["dana@alpha.example"]
    assert ps.load_latest("acme", content_root=tmp_path)["items"][0]["status"] == retired_status


def test_mark_replied_reports_an_unmatched_email_without_erroring(tmp_path):
    _write_latest(
        tmp_path, "acme", [{"domain": "alpha.example", "company": "Alpha", "status": "new"}]
    )
    _write_ready_to_load(
        tmp_path,
        "acme",
        [{"email": "dana@alpha.example", "company": "Alpha", "company_domain": "alpha.example"}],
    )
    # "ghost@nowhere.example" is not in the pool CSV at all.
    summary = ps.mark_replied(
        "acme",
        ["dana@alpha.example", "ghost@nowhere.example"],
        source="test",
        content_root=tmp_path,
    )
    assert summary["matched"] == ["dana@alpha.example"]
    assert summary["unmatched"] == ["ghost@nowhere.example"]


def test_mark_replied_truly_unmatched_when_resolved_key_is_absent_from_the_ledger(tmp_path):
    """A CSV row can resolve to an identity key that no `latest.json` item carries at
    all — e.g. the account was dropped from the ledger, or the pool is stale. This is
    the `set_status`-level `unmatched` (a key `by_key` never saw), distinct from an
    email absent from the CSV entirely (already covered above): here the join
    succeeds, but there is nothing on the other end of it. No ledger write should
    happen for it."""
    _write_latest(
        tmp_path,
        "acme",
        [{"domain": "alpha.example", "company": "Alpha", "status": "new"}],
    )
    _write_ready_to_load(
        tmp_path,
        "acme",
        [{"email": "rep@zeta.example", "company": "Zeta", "company_domain": "zeta.example"}],
    )
    summary = ps.mark_replied("acme", ["rep@zeta.example"], source="test", content_root=tmp_path)
    assert summary["matched"] == []
    assert summary["retired_skipped"] == []
    assert summary["unmatched"] == ["rep@zeta.example"]
    assert summary["changed"] == 0
    item = ps.load_latest("acme", content_root=tmp_path)["items"][0]
    assert item["status"] == "new", "no ledger write should happen for an unmatched key"


def test_mark_replied_no_ready_to_load_csv_reports_unmatched_not_an_error(tmp_path):
    _write_latest(tmp_path, "acme", [{"domain": "alpha.example", "status": "new"}])
    # No ready-to-load.csv written at all for this profile.
    summary = ps.mark_replied("acme", ["dana@alpha.example"], source="test", content_root=tmp_path)
    assert summary["unmatched"] == ["dana@alpha.example"]
    assert summary["matched"] == []


def test_mark_replied_empty_email_list_is_a_no_op(tmp_path):
    _write_latest(tmp_path, "acme", [{"domain": "alpha.example", "status": "new"}])
    summary = ps.mark_replied("acme", [], source="test", content_root=tmp_path)
    assert summary == {
        "profile": "acme",
        "matched": [],
        "retired_skipped": [],
        "unmatched": [],
        "changed": 0,
    }


def test_mark_replied_is_idempotent(tmp_path):
    """Marking the same email replied twice must not error or double-count a change."""
    _write_latest(
        tmp_path, "acme", [{"domain": "alpha.example", "company": "Alpha", "status": "new"}]
    )
    _write_ready_to_load(
        tmp_path,
        "acme",
        [{"email": "dana@alpha.example", "company": "Alpha", "company_domain": "alpha.example"}],
    )
    first = ps.mark_replied("acme", ["dana@alpha.example"], source="test", content_root=tmp_path)
    second = ps.mark_replied("acme", ["dana@alpha.example"], source="test", content_root=tmp_path)
    assert first["changed"] == 1
    assert second["changed"] == 0
    assert second["matched"] == ["dana@alpha.example"], "already-replied still counts as matched"


# --- PS6: end-to-end — a recorded reply is recognized as "engaged" downstream ----- #


def test_a_marked_reply_is_recognized_as_engaged_by_the_hold_trigger(tmp_path):
    """PS6 end-to-end (§R18): proves that marking a reply in latest.json causes
    the engaged-account hold trigger to hold the account on the next routing run.
    Includes negative control: before mark_replied, engaged_account is None.
    """
    from gtm_core.lanes.context import DEFAULT_ENGAGED_STATUSES, load_context
    from gtm_core.lanes.triggers import engaged_account

    _write_latest(
        tmp_path,
        "acme",
        [{"domain": "alpha.example", "company": "Alpha", "status": "new"}],
    )
    _write_ready_to_load(
        tmp_path,
        "acme",
        [{"email": "dana@alpha.example", "company": "Alpha", "company_domain": "alpha.example"}],
    )

    row = {"email": "dana@alpha.example", "company": "Alpha", "company_domain": "alpha.example"}

    # Negative control: before mark_replied, status is "new" and trigger does not hold
    ctx_before = load_context("acme", content_root=tmp_path)
    assert engaged_account(row, ctx_before, None) is None

    ps.mark_replied("acme", ["dana@alpha.example"], source="test", content_root=tmp_path)

    item = ps.load_latest("acme", content_root=tmp_path)["items"][0]
    assert item["status"] == "replied"
    assert item["status"] in DEFAULT_ENGAGED_STATUSES

    # Positive control: after mark_replied, trigger actually fires and returns "engaged-account"
    ctx_after = load_context("acme", content_root=tmp_path)
    hit = engaged_account(row, ctx_after, None)
    assert hit is not None
    assert hit[0] == "engaged-account"
    assert "status=replied" in hit[1]


# --- PS-R C2: mark_replied robust resolution, key precedence, and history logging --- #


def test_c2_mark_replied_resolves_through_master_list(tmp_path):
    _write_latest(
        tmp_path,
        "acme",
        [{"domain": "beta.example", "company": "Beta Corp", "status": "new"}],
    )
    # ready-to-load does not have beta.example, but master-list does
    _write_ready_to_load(tmp_path, "acme", [])
    _write_master_list(
        tmp_path,
        "acme",
        [
            {
                "email": "contacted@beta.example",
                "company": "Beta Corp",
                "company_domain": "beta.example",
            }
        ],
    )
    summary = ps.mark_replied(
        "acme", ["contacted@beta.example"], source="test", content_root=tmp_path
    )
    assert summary["matched"] == ["contacted@beta.example"]
    assert summary["changed"] == 1
    item = ps.load_latest("acme", content_root=tmp_path)["items"][0]
    assert item["status"] == "replied"


def test_c2_mark_replied_resolves_through_cells_toml_registered_csv(tmp_path):
    from gtm_core.prospects_consolidate.paths import _sequences_dir

    _write_latest(
        tmp_path,
        "acme",
        [{"domain": "gamma.example", "company": "Gamma Corp", "status": "new"}],
    )
    _write_ready_to_load(tmp_path, "acme", [])
    _write_master_list(tmp_path, "acme", [])

    seq_dir = _sequences_dir("acme", content_root=tmp_path)
    seq_dir.mkdir(parents=True, exist_ok=True)
    enrolled_csv = seq_dir / "ready-to-load-personalised-2026-09-01.csv"
    with enrolled_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_POOL_COLUMNS)
        writer.writeheader()
        writer.writerow(
            {
                "email": "enrolled@gamma.example",
                "company": "Gamma Corp",
                "company_domain": "gamma.example",
            }
        )

    _write_cells_toml(
        tmp_path,
        "acme",
        [{"id": "SEQ-001", "csv": "ready-to-load-personalised-2026-09-01.csv", "spec": "spec.md"}],
    )

    summary = ps.mark_replied(
        "acme", ["enrolled@gamma.example"], source="test", content_root=tmp_path
    )
    assert summary["matched"] == ["enrolled@gamma.example"]
    assert summary["changed"] == 1
    item = ps.load_latest("acme", content_root=tmp_path)["items"][0]
    assert item["status"] == "replied"


def test_c2_mark_replied_resolves_through_ledger_contact_email(tmp_path):
    _write_latest(
        tmp_path,
        "acme",
        [
            {
                "domain": "delta.example",
                "company": "Delta Corp",
                "contact_email": "lead@delta.example",
                "status": "new",
            }
        ],
    )
    _write_ready_to_load(tmp_path, "acme", [])
    _write_master_list(tmp_path, "acme", [])

    summary = ps.mark_replied("acme", ["lead@delta.example"], source="test", content_root=tmp_path)
    assert summary["matched"] == ["lead@delta.example"]
    assert summary["changed"] == 1
    item = ps.load_latest("acme", content_root=tmp_path)["items"][0]
    assert item["status"] == "replied"


def test_c2_mark_replied_tries_every_identity_key_a_first(tmp_path):
    """When a row has account_id and company_domain that point to different records,
    a: must take precedence over d:."""
    _write_latest(
        tmp_path,
        "acme",
        [
            {
                "account_id": "a-acc1",
                "domain": "domain1.example",
                "company": "Company One",
                "status": "new",
            },
            {
                "account_id": "a-acc2",
                "domain": "domain2.example",
                "company": "Company Two",
                "status": "new",
            },
        ],
    )
    # Row has account_id from acc1, but domain from acc2
    _write_ready_to_load(
        tmp_path,
        "acme",
        [
            {
                "email": "user@domain2.example",
                "account_id": "a-acc1",
                "company_domain": "domain2.example",
            }
        ],
    )

    summary = ps.mark_replied(
        "acme", ["user@domain2.example"], source="test", content_root=tmp_path
    )
    assert summary["matched"] == ["user@domain2.example"]
    items = ps.load_latest("acme", content_root=tmp_path)["items"]
    acc1 = next(it for it in items if it.get("account_id") == "a-acc1")
    acc2 = next(it for it in items if it.get("account_id") == "a-acc2")
    assert acc1["status"] == "replied", "account_id (a:) key must take precedence"
    assert acc2["status"] == "new"


def test_c2_mark_replied_writes_unmatched_and_failed_to_history(tmp_path, monkeypatch):
    _write_latest(tmp_path, "acme", [{"domain": "alpha.example", "status": "new"}])
    _write_ready_to_load(tmp_path, "acme", [])

    summary = ps.mark_replied(
        "acme", ["unknown@nowhere.example"], source="test_sweep", content_root=tmp_path
    )
    assert summary["unmatched"] == ["unknown@nowhere.example"]

    history_file = tmp_path / "acme" / "history.jsonl"
    assert history_file.is_file(), "history.jsonl must be created"
    lines = [
        json.loads(line)
        for line in history_file.read_text(encoding="utf-8").strip().splitlines()
        if line.strip()
    ]
    unmatched_events = [ev for ev in lines if ev.get("event") == "reply_mark_unmatched"]
    assert len(unmatched_events) == 1
    assert unmatched_events[0]["who"] == "unknown@nowhere.example"
    assert unmatched_events[0]["source"] == "test_sweep"

    # Test failed mark logged to history
    def boom(*a, **kw):
        raise RuntimeError("simulated disk error")

    monkeypatch.setattr(ps, "set_status", boom)
    _write_ready_to_load(
        tmp_path, "acme", [{"email": "dana@alpha.example", "company_domain": "alpha.example"}]
    )
    with pytest.raises(RuntimeError):
        ps.mark_replied("acme", ["dana@alpha.example"], source="test_sweep", content_root=tmp_path)

    lines = [
        json.loads(line)
        for line in history_file.read_text(encoding="utf-8").strip().splitlines()
        if line.strip()
    ]
    failed_events = [ev for ev in lines if ev.get("event") == "reply_mark_failed"]
    assert len(failed_events) == 1
    assert failed_events[0]["who"] == "dana@alpha.example"
    assert "simulated disk error" in failed_events[0]["error"]


def test_mutate_account(tmp_path):
    _write_latest(
        tmp_path,
        "acme",
        [
            {
                "id": "acme-corp",
                "domain": "acme.example",
                "company": "Acme Corp",
                "status": "new",
                "lane": "outbound",
            }
        ],
    )
    # Test mutating an account using the mutate_account function directly
    summary = ps.mutate_account(
        "acme",
        "acme.example",  # matches domain
        {"status": "disqualified", "verdict": "drop", "verdict_reason": "operator requested"},
        content_root=tmp_path,
    )
    assert summary["status"] == "ok"
    assert summary["changed"] is True

    data = ps.load_latest("acme", content_root=tmp_path)
    item = data["items"][0]
    assert item["status"] == "disqualified"
    assert item["verdict"] == "drop"
    assert item["verdict_reason"] == "operator requested"
    assert item["lane"] == "outbound"  # untouched field remains

    # Test unmatched account
    summary2 = ps.mutate_account(
        "acme",
        "ghost.example",
        {"status": "disqualified"},
        content_root=tmp_path,
    )
    assert summary2["status"] == "not_found"
    assert summary2["changed"] is False


# --- field-wise merge: a re-emit never destroys what research wrote (PSK-013) --------- #

_RESEARCHED = {
    "id": "contoso-freight",
    "company": "Contoso Freight",
    "domain": "contosofreight.example",
    "score": 9,
    "heat": 2,
    "intent_feeds": ["vibe-topic"],
    "why_now": "Contoso Freight opened its agent platform to partner organisations.",
    "signal_source_url": "https://contosofreight.example/news/partner-agents",
    "contact_email": "rowan.pike@contosofreight.example",
    "verdict": "send",
    "verdict_reason": "",
    "status": "new",
}


def _only_item(root, profile="acme"):
    items = ps.load_latest(profile, content_root=root)["items"]
    assert len(items) == 1, [i.get("company") for i in items]
    return items[0]


def test_a_field_the_caller_left_out_never_erases_a_populated_one(tmp_path):
    _write_latest(tmp_path, "acme", [dict(_RESEARCHED)])
    ps.upsert_latest(
        "acme",
        [{"company": "Contoso Freight", "contact_name": "Rowan Pike"}],
        "run-2",
        content_root=tmp_path,
    )
    after = _only_item(tmp_path)
    for field, value in _RESEARCHED.items():
        assert after[field] == value, f"{field} was erased by an item that never mentioned it"
    assert after["contact_name"] == "Rowan Pike"  # and what it DID supply still lands


@pytest.mark.parametrize("blank", ["", "   ", None, []])
def test_a_blank_incoming_value_never_overwrites_a_populated_one(tmp_path, blank):
    _write_latest(tmp_path, "acme", [dict(_RESEARCHED)])
    ps.upsert_latest(
        "acme",
        [{"company": "Contoso Freight", "why_now": blank, "intent_feeds": blank, "domain": blank}],
        "run-2",
        content_root=tmp_path,
    )
    after = _only_item(tmp_path)
    assert after["why_now"] == _RESEARCHED["why_now"]
    assert after["intent_feeds"] == ["vibe-topic"]
    assert after["domain"] == "contosofreight.example"


def test_a_supplied_zero_is_a_value_not_a_blank(tmp_path):
    """Heat decays: a run that measured 0 must be able to say so."""
    _write_latest(tmp_path, "acme", [dict(_RESEARCHED)])
    ps.upsert_latest(
        "acme", [{"company": "Contoso Freight", "heat": 0}], "run-2", content_root=tmp_path
    )
    assert _only_item(tmp_path)["heat"] == 0


def test_two_rows_for_one_account_in_one_batch_keep_the_union(tmp_path):
    """The export is one row per CONTACT, so one account arrives several times per batch."""
    _write_latest(tmp_path, "acme", [])
    summary = ps.upsert_latest(
        "acme",
        [
            {
                "company": "Contoso Freight",
                "domain": "contosofreight.example",
                "why_now": "Contoso Freight opened its agent platform.",
                "contact_name": "Rowan Pike",
            },
            {"company": "Contoso Freight", "contact_name": "Dana Holt", "contact_title": "CTO"},
        ],
        "run-1",
        content_root=tmp_path,
    )
    assert (summary["added"], summary["updated"]) == (1, 1)
    after = _only_item(tmp_path)
    assert after["why_now"] == "Contoso Freight opened its agent platform."
    assert after["domain"] == "contosofreight.example"
    assert after["contact_title"] == "CTO"


def test_the_verdict_is_untouched_by_an_item_that_supplies_none(tmp_path):
    _write_latest(tmp_path, "acme", [dict(_RESEARCHED)])
    ps.upsert_latest(
        "acme",
        [{"company": "Contoso Freight", "verdict": "", "verdict_reason": "no why-now this pass"}],
        "run-2",
        content_root=tmp_path,
    )
    after = _only_item(tmp_path)
    assert (after["verdict"], after["verdict_reason"]) == ("send", "")


def test_a_supplied_verdict_replaces_the_pair_not_half_of_it(tmp_path):
    _write_latest(
        tmp_path,
        "acme",
        [{**_RESEARCHED, "verdict": "drop", "verdict_reason": "recorded as a competitor"}],
    )
    ps.upsert_latest(
        "acme", [{"company": "Contoso Freight", "verdict": "send"}], "run-2", content_root=tmp_path
    )
    after = _only_item(tmp_path)
    assert (after["verdict"], after["verdict_reason"]) == ("send", ""), (
        "a changed verdict kept the OLD verdict's reason"
    )


def test_a_restated_verdict_keeps_its_reason(tmp_path):
    _write_latest(
        tmp_path, "acme", [{**_RESEARCHED, "verdict": "re-angle", "verdict_reason": "stale clause"}]
    )
    ps.upsert_latest(
        "acme",
        [{"company": "Contoso Freight", "verdict": "re-angle"}],
        "run-2",
        content_root=tmp_path,
    )
    assert _only_item(tmp_path)["verdict_reason"] == "stale clause"


def test_new_account_defaults_reach_a_new_account_and_never_an_existing_one(tmp_path):
    _write_latest(tmp_path, "acme", [dict(_RESEARCHED)])

    def defaults(item):
        return {"score": 0, "segment": "startup", **item}

    ps.upsert_latest(
        "acme",
        [{"company": "Contoso Freight"}, {"company": "Northwind Robotics"}],
        "run-2",
        content_root=tmp_path,
        new_account_defaults=defaults,
    )
    by_company = {i["company"]: i for i in ps.load_latest("acme", content_root=tmp_path)["items"]}
    assert by_company["Northwind Robotics"]["segment"] == "startup"
    assert by_company["Contoso Freight"]["score"] == 9
    assert "segment" not in by_company["Contoso Freight"]


def test_an_existing_accounts_id_is_not_rewritten_by_a_later_run(tmp_path):
    """A pre-slugify ledger id names an account folder; a re-derived slug must not orphan it."""
    _write_latest(tmp_path, "acme", [{"id": "contoso-frt", "company": "Contoso Freight"}])
    ps.upsert_latest(
        "acme",
        [{"id": "contoso-freight", "company": "Contoso Freight", "score": 7}],
        "run-2",
        content_root=tmp_path,
    )
    after = _only_item(tmp_path)
    assert (after["id"], after["score"]) == ("contoso-frt", 7)


# --- a name variant is the same account (duplicate-account defect) -------------------- #


def test_a_suffix_variant_of_the_name_merges_into_the_existing_account(tmp_path):
    _write_latest(
        tmp_path,
        "acme",
        [
            {
                "id": "northwind-robotics-inc",
                "company": "Northwind Robotics, Inc.",
                "status": "do-not-contact",
                "why_now": "Northwind Robotics shipped a fleet agent.",
            }
        ],
    )
    summary = ps.upsert_latest(
        "acme",
        [{"id": "northwind-robotics", "company": "Northwind Robotics", "score": 8}],
        "run-2",
        content_root=tmp_path,
    )
    assert (summary["added"], summary["updated"]) == (0, 1)
    after = _only_item(tmp_path)
    assert after["status"] == "do-not-contact", "the operator's exclusion sat on an orphan"
    assert after["score"] == 8
    # Older pooled rows join on the exact name: a lossy match recognises, never renames.
    assert after["company"] == "Northwind Robotics, Inc."


def test_a_name_variant_in_the_same_batch_is_one_account(tmp_path):
    _write_latest(tmp_path, "acme", [])
    ps.upsert_latest(
        "acme",
        [{"company": "Northwind Robotics, Inc."}, {"company": "Northwind Robotics"}],
        "run-1",
        content_root=tmp_path,
    )
    _only_item(tmp_path)


def test_a_name_variant_never_merges_across_two_different_domains(tmp_path):
    """The lossy key may only ever ADD a match the precise keys missed — a differing
    domain is proof of a different company, and outranks a suffix-stripped name."""
    _write_latest(
        tmp_path,
        "acme",
        [{"company": "Northwind Inc", "domain": "northwind.example", "status": "customer"}],
    )
    summary = ps.upsert_latest(
        "acme",
        [{"company": "Northwind LLC", "domain": "northwind-llc.example"}],
        "run-2",
        content_root=tmp_path,
    )
    assert (summary["added"], summary["updated"]) == (1, 0)


def test_an_ambiguous_name_variant_is_appended_not_guessed(tmp_path):
    _write_latest(
        tmp_path,
        "acme",
        [
            {"company": "Northwind Inc", "domain": "northwind.example"},
            {"company": "Northwind LLC", "domain": "northwind-llc.example"},
        ],
    )
    summary = ps.upsert_latest("acme", [{"company": "Northwind"}], "run-2", content_root=tmp_path)
    assert (summary["added"], summary["updated"]) == (1, 0)


def test_names_that_normalise_to_nothing_are_never_the_same_account(tmp_path):
    """A pure non-ASCII name strips to an empty lossy key; empty must match nothing."""
    _write_latest(tmp_path, "acme", [{"company": "山田商事株式会社", "domain": "yamada.example"}])
    summary = ps.upsert_latest("acme", [{"company": "宏遠"}], "run-2", content_root=tmp_path)
    assert (summary["added"], summary["updated"]) == (1, 0)


# --- the name fallback looks past a LEGAL FORM, never past a word that names a company - #


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Zephyrine Holdings", "Zephyrine Group"),
        ("Zephyrine Holdings", "Zephyrine Co"),
        ("Zephyrine Company", "Zephyrine"),
        ("Zephyrine Group Ltd", "Zephyrine Ltd"),
    ],
)
def test_names_that_differ_by_more_than_a_legal_form_are_two_accounts(tmp_path, first, second):
    """A thin re-discovery carries no domain, so the domain veto cannot save it: "Holdings"
    and "Group" merged, and one company carried the other's contact, why-now and score."""
    summary = ps.upsert_latest(
        "acme",
        [
            {
                "company": first,
                "domain": "zephyrine-holdings.example",
                "score": 9,
                "contact_email": "ada.quill@zephyrine-holdings.example",
                "why_now": "raised a round",
                "verdict": "send",
            },
            {
                "company": second,
                "score": 6,
                "contact_email": "bo.marsh@zephyrine-group.example",
                "why_now": "opened an office",
            },
        ],
        "run-1",
        content_root=tmp_path,
    )
    assert (summary["added"], summary["updated"]) == (2, 0)
    by_company = {i["company"]: i for i in ps.load_latest("acme", content_root=tmp_path)["items"]}
    assert by_company[first]["contact_email"] == "ada.quill@zephyrine-holdings.example"
    assert (by_company[first]["why_now"], by_company[first]["score"]) == ("raised a round", 9)
    assert "domain" not in by_company[second] or not by_company[second]["domain"]


@pytest.mark.parametrize(
    "variant",
    [
        "Contoso Freight",
        "contoso freight inc",
        "Contoso Freight Incorporated",
        "CONTOSO FREIGHT LLC",
        "Contoso Freight Pte. Ltd.",
        "Contoso Freight GmbH",
        "Contoso Freight S.A.",
        "Contoso Freight B.V.",
        "Contoso Freight Pty Limited",
    ],
)
def test_a_legal_form_variant_is_still_the_same_account(tmp_path, variant):
    _write_latest(
        tmp_path,
        "acme",
        [{"company": "Contoso Freight, Inc.", "domain": "contosofreight.example"}],
    )
    summary = ps.upsert_latest("acme", [{"company": variant}], "run-2", content_root=tmp_path)
    assert (summary["added"], summary["updated"]) == (0, 1)
    assert _only_item(tmp_path)["company"] == "Contoso Freight, Inc."


# --- on_merged: the caller learns which account each item landed on, BEFORE the write -- #


def test_on_merged_hands_back_the_ledger_row_each_item_landed_on(tmp_path):
    _write_latest(
        tmp_path,
        "acme",
        [
            {
                "company": "Contoso Freight, Inc.",
                "domain": "contosofreight.example",
                "status": "do-not-contact",
                "account_id": "a-0000000001",
            }
        ],
    )
    seen: list[list[dict]] = []
    items = [
        {"company": "Contoso Freight", "contact_email": "ines.vale@contosofreight.example"},
        {"company": "Northwind Robotics", "domain": "northwind.example"},
    ]
    ps.upsert_latest("acme", items, "run-2", content_root=tmp_path, on_merged=seen.append)

    (landed,) = seen
    assert len(landed) == len(items)
    assert landed[0]["company"] == "Contoso Freight, Inc."
    assert landed[0]["domain"] == "contosofreight.example"
    assert landed[0]["status"] == "do-not-contact"
    assert landed[0]["account_id"] == "a-0000000001"
    assert landed[1]["company"] == "Northwind Robotics"
    assert landed[1]["account_id"], "a new account's id is stamped before the caller sees it"
    written = {i["company"]: i for i in ps.load_latest("acme", content_root=tmp_path)["items"]}
    assert written["Northwind Robotics"]["account_id"] == landed[1]["account_id"]


def test_on_merged_raising_leaves_the_ledger_untouched(tmp_path):
    path = _write_latest(tmp_path, "acme", [{"company": "Contoso Freight"}])
    before = path.read_bytes()

    def refuse(_landed):
        raise ValueError("the export cannot be built")

    with pytest.raises(ValueError, match="export cannot be built"):
        ps.upsert_latest(
            "acme", [{"company": "Northwind Robotics"}], "run-2", content_root=tmp_path,
            on_merged=refuse,
        )  # fmt: skip
    assert path.read_bytes() == before
    assert not (path.parent / ps.SNAPSHOT_DIRNAME).exists()


# --- every read-modify-write of the ledger is serialised ------------------------------- #


def _hold_ledger_lock(ledger: str, ready, release) -> None:
    from pathlib import Path

    from gtm_core.prospects_lock import ledger_lock

    with ledger_lock(Path(ledger), create=True):
        ready.set()
        release.wait(timeout=10)


def test_a_status_write_landing_mid_merge_is_not_lost(tmp_path, monkeypatch):
    """The lost update: writer A reads, the operator's do-not-contact lands, A writes the
    file it computed from the stale read. Serialised, the status write waits for A."""
    import threading

    _write_latest(tmp_path, "acme", [{"company": "Contoso Freight", "domain": "contoso.example"}])
    a_has_read, b_done = threading.Event(), threading.Event()
    real_load = ps.load_latest

    def slow_load(profile, content_root=None):
        data = real_load(profile, content_root)
        if threading.current_thread().name == "writer-a":
            a_has_read.set()
            b_done.wait(timeout=1.0)  # unserialised, B finishes inside this window
        return data

    monkeypatch.setattr(ps, "load_latest", slow_load)

    def writer_a():
        ps.upsert_latest(
            "acme", [{"company": "Northwind Robotics"}], "run-2", content_root=tmp_path
        )

    def writer_b():
        a_has_read.wait(timeout=5)
        ps.set_status("acme", {"d:contoso.example": "do-not-contact"}, content_root=tmp_path)
        b_done.set()

    threads = [
        threading.Thread(target=writer_a, name="writer-a"),
        threading.Thread(target=writer_b, name="writer-b"),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
        assert not t.is_alive(), "a ledger writer deadlocked"

    items = {i["company"]: i for i in real_load("acme", tmp_path)["items"]}
    assert set(items) == {"Contoso Freight", "Northwind Robotics"}
    assert items["Contoso Freight"]["status"] == "do-not-contact", "the status write was lost"


def test_the_ledger_lock_is_reentrant_on_one_path_and_nested_writers_do_not_deadlock(tmp_path):
    import threading

    from gtm_core.prospects_lock import ledger_lock

    def nested():
        with ledger_lock(ps.latest_path("acme", tmp_path), create=True):
            ps.upsert_latest("acme", [{"company": "Contoso Freight"}], "r1", content_root=tmp_path)
            ps.set_status("acme", {"c:contoso freight": "replied"}, content_root=tmp_path)
            ps.mutate_account("acme", "Contoso Freight", {"notes": "x"}, content_root=tmp_path)

    t = threading.Thread(target=nested)
    t.start()
    t.join(timeout=15)
    assert not t.is_alive(), "a nested ledger write blocked on its own lock"
    assert _only_item(tmp_path)["status"] == "replied"


def test_the_ledger_lock_excludes_a_writer_in_another_process(tmp_path):
    import multiprocessing
    import threading

    _write_latest(tmp_path, "acme", [{"company": "Contoso Freight", "domain": "contoso.example"}])
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    holder = ctx.Process(
        target=_hold_ledger_lock, args=(str(ps.latest_path("acme", tmp_path)), ready, release)
    )
    holder.start()
    try:
        assert ready.wait(timeout=20)
        done = threading.Event()

        def write():
            ps.set_status("acme", {"d:contoso.example": "replied"}, content_root=tmp_path)
            done.set()

        t = threading.Thread(target=write)
        t.start()
        assert not done.wait(timeout=0.5), "the write went through a lock another process held"
        release.set()
        assert done.wait(timeout=15), "the write never ran once the lock was released"
        t.join(timeout=5)
    finally:
        release.set()
        holder.join(timeout=10)
        if holder.is_alive():
            holder.terminate()
    assert _only_item(tmp_path)["status"] == "replied"


def test_a_status_write_on_a_tenant_with_no_ledger_invents_no_folder(tmp_path):
    summary = ps.set_status("acme", {"d:contoso.example": "replied"}, content_root=tmp_path)
    assert summary["unmatched"] == ["d:contoso.example"]
    assert list(tmp_path.iterdir()) == []


def test_a_scorer_drop_is_lifted_when_the_row_later_scores_into_a_published_tier(tmp_path):
    from gtm_core.prospects_merge import merge_onto

    dropped = {"company": "Northwind Robotics", "tier": "drop", "verdict": "drop",
               "verdict_reason": "below publish threshold", "lane": "excluded"}  # fmt: skip
    lifted = merge_onto(dropped, {"company": "Northwind Robotics", "tier": "A", "score": 9})
    assert (lifted["verdict"], lifted["verdict_reason"], lifted["lane"]) == ("", "", "")

    researcher = {**dropped, "verdict_reason": "direct competitor"}
    kept = merge_onto(researcher, {"company": "Northwind Robotics", "tier": "A", "score": 9})
    assert (kept["verdict"], kept["verdict_reason"]) == ("drop", "direct competitor")

    still_low = merge_onto(dropped, {"company": "Northwind Robotics", "tier": "drop", "score": 2})
    assert still_low["verdict"] == "drop"


def test_mutate_cli_stamps_the_date_a_verdict_is_set(tmp_path, monkeypatch, capsys):
    """Setting `verdict` by hand is research setting it, so it carries `verdict_on` — the
    stamp `prospects_consolidate` compares before lifting a row's re-angle. An explicit
    `verdict_on` wins, and a mutate that does not touch the verdict stamps nothing."""
    import datetime

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    _write_latest(
        tmp_path,
        "acme",
        [
            {"account_id": "a-1", "company": "Northwind", "domain": "northwind.example"},
            {"account_id": "a-2", "company": "Fabrikam", "domain": "fabrikam.example"},
            {"account_id": "a-3", "company": "Litware", "domain": "litware.example"},
        ],
    )
    base = ["mutate", "--profile", "acme", "--account"]
    assert ps._cli([*base, "a-1", "--set", "verdict=send", "--reason", "fresh signal"]) == 0
    assert ps._cli([*base, "a-2", "--set", "verdict=send", "--set", "verdict_on=2026-09-20"]) == 0
    assert ps._cli([*base, "a-3", "--set", "notes=x"]) == 0
    items = {i["account_id"]: i for i in ps.load_latest("acme", content_root=tmp_path)["items"]}
    assert items["a-1"]["verdict_on"] == datetime.date.today().isoformat()
    assert items["a-2"]["verdict_on"] == "2026-09-20"
    assert "verdict_on" not in items["a-3"]


def test_fill_accounts_one_snapshot_per_call(tmp_path):
    _write_latest(tmp_path, "acme", [{"id": "a", "company": "Alpha", "industry": ""}])
    snaps_before = (
        list((tmp_path / "acme/prospects/.snapshots").glob("latest-*.json"))
        if (tmp_path / "acme/prospects/.snapshots").exists()
        else []
    )

    summary = ps.fill_accounts(
        "acme", [{"id": "a", "industry": "Software"}], source="vibe", content_root=tmp_path
    )

    snaps_after = list((tmp_path / "acme/prospects/.snapshots").glob("latest-*.json"))
    assert len(snaps_after) == len(snaps_before) + 1
    assert summary["snapshot"] is not None


def test_fill_accounts_populated_field_is_unchanged(tmp_path):
    _write_latest(tmp_path, "acme", [{"id": "a", "company": "Alpha", "industry": "Hardware"}])

    summary = ps.fill_accounts(
        "acme",
        [{"id": "a", "industry": "Software", "city": "SF"}],
        source="vibe",
        content_root=tmp_path,
    )

    data = ps.load_latest("acme", content_root=tmp_path)
    item = data["items"][0]
    assert item["industry"] == "Hardware"
    assert item["city"] == "SF"
    assert len(summary["conflicts"]) == 1
    assert summary["conflicts"][0]["field"] == "industry"


def test_fill_accounts_zero_or_two_matches_refuses_row(tmp_path):
    _write_latest(
        tmp_path,
        "acme",
        [
            {"id": "a", "domain": "dup.example", "industry": ""},
            {"id": "b", "domain": "dup.example", "industry": ""},
        ],
    )

    summary = ps.fill_accounts(
        "acme",
        [
            {"domain": "dup.example", "industry": "Software"},  # two matches
            {"domain": "none.example", "industry": "Software"},  # zero matches
        ],
        source="vibe",
        content_root=tmp_path,
    )

    data = ps.load_latest("acme", content_root=tmp_path)
    assert data["items"][0]["industry"] == ""
    assert data["items"][1]["industry"] == ""

    assert len(summary["refused"]) == 2


import threading


def test_fill_accounts_runs_under_lock(tmp_path):
    _write_latest(
        tmp_path,
        "acme",
        [
            {"id": "a", "company": "Alpha", "industry": ""},
            {"id": "b", "company": "Beta", "industry": ""},
        ],
    )
    barrier = threading.Barrier(2)
    errors = []

    def worker_a():
        try:
            barrier.wait()
            ps.fill_accounts(
                "acme",
                [{"id": "a", "industry": "Aerospace"}],
                source="test_a",
                content_root=tmp_path,
            )
        except Exception as exc:
            errors.append(exc)

    def worker_b():
        try:
            barrier.wait()
            ps.fill_accounts(
                "acme", [{"id": "b", "industry": "Biotech"}], source="test_b", content_root=tmp_path
            )
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=worker_a)
    t2 = threading.Thread(target=worker_b)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"Concurrent workers encountered errors: {errors}"
    data = ps.load_latest("acme", content_root=tmp_path)
    by_id = {item["id"]: item["industry"] for item in data.get("items", [])}
    assert by_id.get("a") == "Aerospace", "Thread 1 update lost"
    assert by_id.get("b") == "Biotech", "Thread 2 update lost"
