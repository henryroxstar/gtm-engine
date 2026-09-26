"""Unit tests for snapshot traversal, grouping and retention in gtm_core.snapshots."""

from pathlib import Path

from gtm_core.snapshots import group_snapshots_by_topic


def test_group_snapshots_finds_nested_snapshots(tmp_path: Path) -> None:
    snap_dir = tmp_path / "content" / "acme" / ".snapshots" / "knowledge"
    nested_dir = snap_dir / "guidance"
    nested_dir.mkdir(parents=True, exist_ok=True)

    snap_file = nested_dir / "01-nist.md.20260901T000000-000000Z"
    snap_file.write_text("guidance facts", encoding="utf-8")

    groups = group_snapshots_by_topic(snap_dir)
    assert "guidance/01-nist.md" in groups
    assert snap_file in groups["guidance/01-nist.md"]


def test_group_snapshots_disambiguates_products_with_same_filename(tmp_path: Path) -> None:
    snap_dir = tmp_path / "content" / "acme" / ".snapshots" / "knowledge"
    dir_a = snap_dir / "prod_a"
    dir_b = snap_dir / "prod_b"
    dir_a.mkdir(parents=True, exist_ok=True)
    dir_b.mkdir(parents=True, exist_ok=True)

    snap_a = dir_a / "rules.md.20260901T000000-000000Z"
    snap_b = dir_b / "rules.md.20260901T000000-000000Z"
    snap_a.write_text("rules for prod a", encoding="utf-8")
    snap_b.write_text("rules for prod b", encoding="utf-8")

    groups = group_snapshots_by_topic(snap_dir)
    assert "prod_a/rules.md" in groups
    assert "prod_b/rules.md" in groups
    assert groups["prod_a/rules.md"] == [snap_a]
    assert groups["prod_b/rules.md"] == [snap_b]
