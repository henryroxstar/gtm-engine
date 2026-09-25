"""PS20 T1.11 — the sequencer snapshot says when it can't be trusted, instead of reading as 0."""

import json

import pytest

from gtm_core import prospects_dashboard as pd


def _pool(tmp_path, profile="acme"):
    pool = pd._pool_dir(profile, tmp_path)
    pool.mkdir(parents=True, exist_ok=True)
    return pool


def test_rows_and_fetched_are_read(tmp_path):
    _pool(tmp_path).joinpath("sequence-stats.json").write_text(
        json.dumps(
            {"fetched": "2026-09-10T03:50:38Z", "sequences": [{"id": "S1", "name": "A", "sent": 3}]}
        ),
        encoding="utf-8",
    )
    snap = pd.load_sequence_snapshot("acme", tmp_path)
    assert [r["id"] for r in snap["rows"]] == ["S1"]
    assert (snap["fetched"], snap["unreadable"], snap["skipped"], snap["source"]) == (
        "2026-09-10T03:50:38Z",
        False,
        0,
        "stats",
    )


def test_stats_file_as_plain_list_returns_rows(tmp_path):
    _pool(tmp_path).joinpath("sequence-stats.json").write_text(
        json.dumps([{"id": "S1", "sent": 1}]), encoding="utf-8"
    )
    snap = pd.load_sequence_snapshot("acme", tmp_path)
    assert [r["id"] for r in snap["rows"]] == ["S1"]
    assert (snap["fetched"], snap["unreadable"], snap["source"]) == (None, False, "stats")


def test_invalid_json_is_unreadable_and_never_falls_back(tmp_path):
    pool = _pool(tmp_path)
    pool.joinpath("sequence-stats.json").write_text("{not json", encoding="utf-8")
    pool.joinpath("sequence-state.json").write_text(
        json.dumps({"id": "OLD", "sent": 9}), encoding="utf-8"
    )
    snap = pd.load_sequence_snapshot("acme", tmp_path)
    assert (snap["rows"], snap["unreadable"], snap["source"]) == ([], True, "stats")


@pytest.mark.parametrize("payload", ["7", '"text"', json.dumps({"no_sequences_key": []})])
def test_wrong_shape_is_unreadable(tmp_path, payload):
    pool = _pool(tmp_path)
    pool.joinpath("sequence-stats.json").write_text(payload, encoding="utf-8")
    # A valid state file must NOT be used as a fallback after a wrong-shape stats file.
    pool.joinpath("sequence-state.json").write_text(
        json.dumps({"id": "OLD", "sent": 9}), encoding="utf-8"
    )
    snap = pd.load_sequence_snapshot("acme", tmp_path)
    assert (snap["rows"], snap["unreadable"], snap["source"]) == ([], True, "stats")


def test_non_dict_rows_are_counted_not_silently_dropped(tmp_path):
    _pool(tmp_path).joinpath("sequence-stats.json").write_text(
        json.dumps({"sequences": [{"id": "S1", "sent": 1}, "junk", 5]}), encoding="utf-8"
    )
    snap = pd.load_sequence_snapshot("acme", tmp_path)
    assert (len(snap["rows"]), snap["skipped"], snap["unreadable"]) == (1, 2, False)


def test_absent_stats_falls_back_to_state_file(tmp_path):
    _pool(tmp_path).joinpath("sequence-state.json").write_text(
        json.dumps({"id": "OLD", "sent": 9}), encoding="utf-8"
    )
    snap = pd.load_sequence_snapshot("acme", tmp_path)
    assert ([r["id"] for r in snap["rows"]], snap["source"], snap["fetched"]) == (
        ["OLD"],
        "state",
        None,
    )


def test_corrupt_state_file_is_unreadable(tmp_path):
    _pool(tmp_path).joinpath("sequence-state.json").write_text("{bad", encoding="utf-8")
    snap = pd.load_sequence_snapshot("acme", tmp_path)
    assert (snap["rows"], snap["unreadable"], snap["source"]) == ([], True, "state")


def test_non_object_state_file_is_unreadable(tmp_path):
    _pool(tmp_path).joinpath("sequence-state.json").write_text("[1, 2]", encoding="utf-8")
    snap = pd.load_sequence_snapshot("acme", tmp_path)
    assert (snap["rows"], snap["unreadable"], snap["source"]) == ([], True, "state")


def test_no_files_is_empty_not_unreadable(tmp_path):
    snap = pd.load_sequence_snapshot("acme", tmp_path)
    assert (snap["rows"], snap["unreadable"], snap["source"]) == ([], False, None)


def test_wrapper_and_status_model_carry_the_same_rows(tmp_path):
    _pool(tmp_path).joinpath("sequence-stats.json").write_text(
        json.dumps({"fetched": "2026-09-20", "sequences": [{"id": "S1", "sent": 2}]}),
        encoding="utf-8",
    )
    assert [r["id"] for r in pd._load_sequences("acme", tmp_path)] == ["S1"]
    assert pd.build_status("acme", tmp_path)["snapshot"] == {
        "fetched": "2026-09-20",
        "unreadable": False,
        "skipped": 0,
        "source": "stats",
    }
