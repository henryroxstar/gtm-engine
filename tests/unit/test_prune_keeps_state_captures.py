"""`signal_sources prune` never deletes a capture a member-set state file still points at (R1.4)."""

from __future__ import annotations

import datetime

import pytest

from gtm_core.signal_obs import state
from gtm_core.signal_sources import get_captures, prune, store_capture

pytestmark = pytest.mark.usefixtures("switch_open")

URL = "https://members.example.test/list"
OLD = "2026-01-01T08:00:00+00:00"
AS_OF = datetime.date(2026, 10, 1)


def _setup(tmp_path, *, referenced: bool):
    sources = tmp_path / "content" / "acme" / "sources"
    sha = store_capture(
        URL,
        "# Members\n\n[Northwind Traders](https://n.example.test)\n",
        sources_dir=sources,
        fetched_at=OLD,
    )
    obs_dir = tmp_path / "content" / "acme" / "prospects" / "observations"
    if referenced:
        state.save_state(
            obs_dir,
            "alpha",
            "north-directory",
            {"status": "ok", "capture_sha256": sha, "members": {"x": {}}},
        )
    return sources, sha


def test_an_old_capture_a_state_file_points_at_survives_prune(tmp_path):
    sources, sha = _setup(tmp_path, referenced=True)
    removed = prune(sources_dir=sources, older_than_days=30, as_of=AS_OF, apply=True)
    assert removed == [] and (sources / f"{sha}.txt").is_file()
    assert [c.sha256 for c in get_captures(URL, sources_dir=sources)] == [sha]


def test_an_unreferenced_old_capture_is_still_pruned(tmp_path):
    sources, sha = _setup(tmp_path, referenced=False)
    removed = prune(sources_dir=sources, older_than_days=30, as_of=AS_OF, apply=True)
    assert [p.stem for p in removed] == [sha] and not (sources / f"{sha}.txt").exists()


def test_the_plan_without_apply_does_not_list_a_protected_capture(tmp_path):
    sources, _ = _setup(tmp_path, referenced=True)
    assert prune(sources_dir=sources, older_than_days=30, as_of=AS_OF, apply=False) == []


@pytest.mark.parametrize(
    "body",
    ["{not json", "[]", '{"capture_sha256": 7}', '{"capture_sha256": "../x"}', "{}"],
)
def test_a_damaged_state_file_makes_prune_delete_nothing_and_say_why(tmp_path, body):
    sources, sha = _setup(tmp_path, referenced=False)
    bad = tmp_path / "content" / "acme" / "prospects" / "observations" / "state" / "beta"
    bad.mkdir(parents=True)
    (bad / "north-directory.json").write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match="nothing deleted"):
        prune(sources_dir=sources, older_than_days=30, as_of=AS_OF, apply=True)
    assert (sources / f"{sha}.txt").is_file()


def test_the_prune_guard_reads_every_products_state_not_only_one(tmp_path):
    sources, sha = _setup(tmp_path, referenced=False)
    obs_dir = tmp_path / "content" / "acme" / "prospects" / "observations"
    state.save_state(
        obs_dir, "beta", "other-source", {"status": "ok", "capture_sha256": sha, "members": {}}
    )
    assert prune(sources_dir=sources, older_than_days=30, as_of=AS_OF, apply=True) == []
    assert (sources / f"{sha}.txt").is_file()


def test_prune_from_the_command_line_reports_a_refusal_and_exits_nonzero(tmp_path, capsys):
    from gtm_core import signal_sources

    sources, _ = _setup(tmp_path, referenced=False)
    bad = tmp_path / "content" / "acme" / "prospects" / "observations" / "state" / "beta"
    bad.mkdir(parents=True)
    (bad / "x.json").write_text("{nope", encoding="utf-8")
    code = signal_sources.main(
        ["prune", "--sources-dir", str(sources), "--older-than", "30", "--apply"]
    )
    assert code == 1 and "nothing deleted" in capsys.readouterr().err


def test_a_protected_capture_with_no_index_row_is_not_deleted_as_an_orphan(tmp_path):
    sources, sha = _setup(tmp_path, referenced=False)
    (sources / "index.jsonl").write_text("", encoding="utf-8")  # the row is gone, the page stays
    obs_dir = tmp_path / "content" / "acme" / "prospects" / "observations"
    state.save_state(
        obs_dir, "alpha", "north-directory", {"status": "ok", "capture_sha256": sha, "members": {}}
    )
    assert prune(sources_dir=sources, older_than_days=30, as_of=AS_OF, apply=True) == []
    assert (sources / f"{sha}.txt").is_file()


def test_a_symlinked_state_directory_makes_prune_delete_nothing(tmp_path):
    sources, sha = _setup(tmp_path, referenced=False)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "north-directory.json").write_text(
        f'{{"capture_sha256": "{sha}"}}', encoding="utf-8"
    )
    state_dir = tmp_path / "content" / "acme" / "prospects" / "observations" / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "beta").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match="nothing deleted"):
        prune(sources_dir=sources, older_than_days=30, as_of=AS_OF, apply=True)
    assert (sources / f"{sha}.txt").is_file()


def test_an_old_capture_referenced_in_observation_shard_survives_prune(tmp_path):
    import json

    sources, sha = _setup(tmp_path, referenced=False)
    obs_dir = tmp_path / "content" / "acme" / "prospects" / "observations"
    obs_dir.mkdir(parents=True, exist_ok=True)
    shard = obs_dir / "writer.run.jsonl"
    record = {
        "schema": 1,
        "kind": "source_member",
        "product": "alpha",
        "source_id": "north-directory",
        "capture_sha256": sha,
        "account_key": "northwind.example.test",
        "observed": "2026-01-01",
    }
    shard.write_text(json.dumps(record) + "\n", encoding="utf-8")
    removed = prune(sources_dir=sources, older_than_days=30, as_of=AS_OF, apply=True)
    assert removed == [] and (sources / f"{sha}.txt").is_file()
