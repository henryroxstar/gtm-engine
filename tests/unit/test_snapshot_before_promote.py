"""Tests for snapshot-before-promote and restore in knowledge staging (R-14)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from gtm_core import knowledge_staging as ks
from gtm_core.snapshots import prune_snapshots

TODAY = date(2026, 6, 1)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _profile(tmp_path: Path, name: str = "acme") -> tuple[Path, Path]:
    prof = tmp_path / "profiles" / name
    _write(prof / "PROFILE.md", "name: Acme\n")
    return tmp_path / "profiles", tmp_path / "content"


def test_promote_snapshots_before_overwriting(tmp_path: Path) -> None:
    profiles_root, content_root = _profile(tmp_path)
    live = ks.live_path(profiles_root, "acme", "company")
    _write(live, "version 1 facts\n")

    # Stage candidate
    candidate = (
        "---\nsource: manual\nrefreshed: 2026-01-01\nreview: 90d\n---\n# Company\nversion 2 facts\n"
    )
    ks.stage(content_root, "acme", "company", candidate)

    # Promote
    target = ks.promote(profiles_root, content_root, "acme", "company", today=TODAY)
    assert target == live
    assert "version 2 facts" in live.read_text(encoding="utf-8")

    # Verify snapshot was created in content/<profile>/.snapshots/knowledge/
    snap_dir = content_root / "acme" / ".snapshots" / "knowledge"
    assert snap_dir.is_dir()
    snaps = sorted(snap_dir.glob("company.*"))
    assert len(snaps) == 1
    assert snaps[0].read_text(encoding="utf-8") == "version 1 facts\n"


def test_promote_new_topic_without_live_file_creates_no_snapshot(tmp_path: Path) -> None:
    profiles_root, content_root = _profile(tmp_path)
    candidate = (
        "---\nsource: manual\nrefreshed: 2026-01-01\nreview: 90d\n---\n# New\nbrand new topic\n"
    )
    ks.stage(content_root, "acme", "new_topic", candidate)

    ks.promote(profiles_root, content_root, "acme", "new_topic", today=TODAY)
    live = ks.live_path(profiles_root, "acme", "new_topic")
    assert live.exists()

    snap_dir = content_root / "acme" / ".snapshots" / "knowledge"
    snaps = list(snap_dir.glob("new_topic.*")) if snap_dir.exists() else []
    assert len(snaps) == 0


def test_restore_replaces_live_and_is_reversible(tmp_path: Path) -> None:
    profiles_root, content_root = _profile(tmp_path)
    live = ks.live_path(profiles_root, "acme", "company")
    _write(live, "version 1 facts\n")

    # Promote v2
    ks.stage(
        content_root,
        "acme",
        "company",
        "---\nsource: manual\nrefreshed: 2026-01-01\nreview: 90d\n---\n# Company\nversion 2 facts\n",
    )
    t0 = datetime(2026, 6, 1, 12, 0, 0, 0, tzinfo=UTC)
    ks.promote(profiles_root, content_root, "acme", "company", today=TODAY, now=t0)
    assert "version 2 facts" in live.read_text(encoding="utf-8")

    # Restore latest snapshot (which should be v1)
    t1 = t0 + timedelta(seconds=1)
    restored_from = ks.restore(profiles_root, content_root, "acme", "company", now=t1)
    assert restored_from.read_text(encoding="utf-8") == "version 1 facts\n"
    assert live.read_text(encoding="utf-8") == "version 1 facts\n"

    # Verify reversibility: restore snapshotted v2 before overwriting
    snap_dir = content_root / "acme" / ".snapshots" / "knowledge"
    snaps = sorted(snap_dir.glob("company.*"))
    assert len(snaps) == 2
    # The newest snapshot contains what was live right before restore (v2)
    assert "version 2 facts" in snaps[-1].read_text(encoding="utf-8")


def test_microsecond_stamps_do_not_collide(tmp_path: Path) -> None:
    profiles_root, content_root = _profile(tmp_path)
    live = ks.live_path(profiles_root, "acme", "company")
    _write(live, "v1\n")

    t0 = datetime(2026, 6, 1, 12, 0, 0, 100, tzinfo=UTC)
    t1 = datetime(2026, 6, 1, 12, 0, 0, 101, tzinfo=UTC)

    # First promote at t0
    ks.stage(
        content_root,
        "acme",
        "company",
        "---\nsource: manual\nrefreshed: 2026-01-01\nreview: 90d\n---\n# Company\nv2\n",
    )
    ks.promote(profiles_root, content_root, "acme", "company", today=TODAY, now=t0)

    # Second promote at t1 (same second, 1 microsecond later)
    ks.stage(
        content_root,
        "acme",
        "company",
        "---\nsource: manual\nrefreshed: 2026-01-01\nreview: 90d\n---\n# Company\nv3\n",
    )
    ks.promote(profiles_root, content_root, "acme", "company", today=TODAY, now=t1)

    snap_dir = content_root / "acme" / ".snapshots" / "knowledge"
    snaps = sorted(snap_dir.glob("company.*"))
    assert len(snaps) == 2
    assert snaps[0].name != snaps[1].name


def test_snapshots_prune_keeps_twenty_per_topic(tmp_path: Path) -> None:
    profiles_root, content_root = _profile(tmp_path)
    snap_dir = content_root / "acme" / ".snapshots" / "knowledge"
    snap_dir.mkdir(parents=True, exist_ok=True)

    # Create 25 snapshots for company and 25 for icp
    base = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    for i in range(25):
        t = base + timedelta(minutes=i)
        stamp = t.strftime("%Y%m%dT%H%M%S-%fZ")
        (snap_dir / f"company.{stamp}").write_text(f"company v{i}\n", encoding="utf-8")
        (snap_dir / f"icp.{stamp}").write_text(f"icp v{i}\n", encoding="utf-8")

    # Dry run
    plan = prune_snapshots(content_root, "acme", keep=20, dry_run=True)
    assert len(plan["to_delete"]) == 10  # 5 company + 5 icp
    # Verify no files were deleted
    assert len(list(snap_dir.glob("company.*"))) == 25
    assert len(list(snap_dir.glob("icp.*"))) == 25

    # Actual prune
    res = prune_snapshots(content_root, "acme", keep=20, dry_run=False)
    assert len(res["deleted"]) == 10
    remaining_company = sorted(snap_dir.glob("company.*"))
    remaining_icp = sorted(snap_dir.glob("icp.*"))
    assert len(remaining_company) == 20
    assert len(remaining_icp) == 20
    # Oldest remaining should be index 5
    assert remaining_company[0].read_text(encoding="utf-8") == "company v5\n"
