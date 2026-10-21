"""Identity survives the round trip: latest.json -> consolidate -> rebuild.

Account identity was implemented six times across this pipeline with mutually
non-derivable semantics, so joining two files meant re-deriving a key from fields one
of them might not carry. Most of the state-loss incidents of the last two months are
that gap: one prospect's status, suppression, and research living under keys that could
not find each other.

These tests pin the property the stamped ids exist to provide — the same account and the
same row keep the same identity across a rebuild, whatever their observable fields do.

All fixtures are invented (docs/RULES.md R9).
"""

from __future__ import annotations

import csv
import json

from gtm_core import prospects_consolidate as pc
from gtm_core import prospects_state as ps
from gtm_core.prospects_state import ACCOUNT_ID_FIELD


def _latest(tmp_path, profile, items):
    p = ps.latest_path(profile, content_root=tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"kind": "prospects", "profile": profile, "items": items}), encoding="utf-8"
    )
    return p


def _export(tmp_path, profile, rows):
    p = tmp_path / profile / "prospects" / "prospects-20260827-hubspot.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    header = ["First Name", "Last Name", "Email", "Company", "Company Domain Name", "Email Status"]
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return p


def _master(tmp_path, profile):
    path = tmp_path / profile / "prospects" / "sequences" / ".pool" / "master-list.csv"
    return list(csv.DictReader(path.open()))


# --- account_id, in the ledger of record ------------------------------------ #


def test_an_account_is_stamped_once_and_keeps_its_id(tmp_path):
    ps.upsert_latest(
        "acme",
        [{"company": "Northwind", "domain": "northwind.example"}],
        "r1",
        content_root=tmp_path,
    )
    first = ps.load_latest("acme", content_root=tmp_path)["items"][0][ACCOUNT_ID_FIELD]
    assert first.startswith("a-")

    ps.upsert_latest(
        "acme",
        [{"company": "Northwind", "domain": "northwind.example", "score": 9}],
        "r2",
        content_root=tmp_path,
    )
    items = ps.load_latest("acme", content_root=tmp_path)["items"]
    assert len(items) == 1
    assert items[0][ACCOUNT_ID_FIELD] == first, "a re-merge reassigned the account's identity"


def test_an_incoming_id_never_overwrites_an_assigned_one(tmp_path):
    ps.upsert_latest("acme", [{"domain": "northwind.example"}], "r1", content_root=tmp_path)
    mine = ps.load_latest("acme", content_root=tmp_path)["items"][0][ACCOUNT_ID_FIELD]
    ps.upsert_latest(
        "acme",
        [{"domain": "northwind.example", ACCOUNT_ID_FIELD: "a-hostile"}],
        "r2",
        content_root=tmp_path,
    )
    assert ps.load_latest("acme", content_root=tmp_path)["items"][0][ACCOUNT_ID_FIELD] == mine


def test_a_file_written_before_ids_existed_gains_them_on_the_next_merge(tmp_path):
    """No migration step: the merge stamps what it finds unstamped."""
    _latest(tmp_path, "acme", [{"company": "Northwind", "domain": "northwind.example"}])
    summary = ps.upsert_latest("acme", [], "r1", content_root=tmp_path)
    assert summary["ids_stamped"] == 1
    assert ps.load_latest("acme", content_root=tmp_path)["items"][0][ACCOUNT_ID_FIELD]


def test_set_status_can_address_an_account_by_its_id(tmp_path):
    ps.upsert_latest("acme", [{"domain": "northwind.example"}], "r1", content_root=tmp_path)
    account_id = ps.load_latest("acme", content_root=tmp_path)["items"][0][ACCOUNT_ID_FIELD]
    summary = ps.set_status("acme", {f"a:{account_id}": "disqualified"}, content_root=tmp_path)
    assert summary["changed"] == 1
    assert ps.load_latest("acme", content_root=tmp_path)["items"][0]["status"] == "disqualified"


# --- row_id + the join, in the derived views -------------------------------- #


def test_rows_are_stamped_and_keep_their_id_across_a_rebuild(tmp_path):
    _export(
        tmp_path,
        "acme",
        [["Dana", "Vance", "dana@northwind.example", "Northwind", "northwind.example", "verified"]],
    )
    pc.consolidate("acme", content_root=tmp_path)
    first = _master(tmp_path, "acme")[0]["pool_row_id"]
    assert first.startswith("r-")

    pc.consolidate("acme", content_root=tmp_path)
    assert _master(tmp_path, "acme")[0]["pool_row_id"] == first, "a rebuild reassigned the row's id"


def test_a_row_joins_to_its_account_in_the_ledger(tmp_path):
    ps.upsert_latest(
        "acme",
        [{"company": "Northwind", "domain": "northwind.example"}],
        "r1",
        content_root=tmp_path,
    )
    account_id = ps.load_latest("acme", content_root=tmp_path)["items"][0][ACCOUNT_ID_FIELD]
    _export(
        tmp_path,
        "acme",
        [["Dana", "Vance", "dana@northwind.example", "Northwind", "northwind.example", "verified"]],
    )
    res = pc.consolidate("acme", content_root=tmp_path)
    assert res["accounts_joined"] == 1
    assert _master(tmp_path, "acme")[0][ACCOUNT_ID_FIELD] == account_id


def test_a_row_with_no_account_is_reported_not_silently_passed(tmp_path):
    """An orphan is a finding. Silently passing one is how the six-key gap stayed invisible."""
    _export(
        tmp_path,
        "acme",
        [["Dana", "Vance", "dana@nowhere.example", "Nowhere", "nowhere.example", "verified"]],
    )
    res = pc.consolidate("acme", content_root=tmp_path)
    assert res["orphan_rows"] == 1
    assert res["accounts_joined"] == 0


def test_identity_survives_the_whole_cycle(tmp_path):
    """The end-to-end property: ids stable, status and suppression still enforced."""
    ps.upsert_latest(
        "acme",
        [{"company": "Northwind", "domain": "northwind.example"}],
        "r1",
        content_root=tmp_path,
    )
    account_id = ps.load_latest("acme", content_root=tmp_path)["items"][0][ACCOUNT_ID_FIELD]
    _export(
        tmp_path,
        "acme",
        [["Dana", "Vance", "dana@northwind.example", "Northwind", "northwind.example", "verified"]],
    )
    pc.consolidate("acme", content_root=tmp_path)
    row_id = _master(tmp_path, "acme")[0]["pool_row_id"]

    # The operator retires the account, addressing it by its stamped id.
    ps.set_status("acme", {f"a:{account_id}": "disqualified"}, content_root=tmp_path)

    res = pc.consolidate("acme", content_root=tmp_path)
    after = _master(tmp_path, "acme")[0]
    assert after["pool_row_id"] == row_id
    assert after[ACCOUNT_ID_FIELD] == account_id
    assert res["disqualified_excluded"] == 1, "the retirement did not reach the build"
    assert res["ready_to_load"] == 0
