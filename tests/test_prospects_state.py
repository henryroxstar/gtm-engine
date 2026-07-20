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
