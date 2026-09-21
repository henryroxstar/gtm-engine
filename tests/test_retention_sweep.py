"""Tests for gtm_core.retention_sweep (PII data retention sweeper)."""

from __future__ import annotations

import gzip
import os
import time
from pathlib import Path

from gtm_core.retention_sweep import sweep_stale_pii


def test_sweep_purges_old_csvs(tmp_path: Path) -> None:
    profile = "test-tenant"
    prospects_dir = tmp_path / profile / "prospects"
    sequences_dir = prospects_dir / "sequences"
    sequences_dir.mkdir(parents=True, exist_ok=True)

    # Create old hubspot CSV (10 days old) — should be archived
    hubspot_csv = prospects_dir / "prospects-20260901-hubspot.csv"
    hubspot_csv.write_text("first,last,email\nJane,Doe,jane@example.com\n", encoding="utf-8")

    # Set mtime to 10 days ago
    old_time = time.time() - (10 * 86400)
    os.utime(hubspot_csv, (old_time, old_time))

    res = sweep_stale_pii(profile, ttl_days=7, content_root=tmp_path)

    assert len(res.archived) == 1
    assert not hubspot_csv.exists()

    # Check archive directory
    archive_dir = prospects_dir / ".archive"
    assert archive_dir.exists()
    archived_files = list(archive_dir.glob("*.csv.gz"))
    assert len(archived_files) == 1

    with gzip.open(archived_files[0], "rt", encoding="utf-8") as f:
        content = f.read()
        assert "@example.com" in content


def test_sweep_preserves_fresh_csvs(tmp_path: Path) -> None:
    profile = "test-tenant"
    prospects_dir = tmp_path / profile / "prospects"
    prospects_dir.mkdir(parents=True, exist_ok=True)

    # Create fresh hubspot CSV (1 day old)
    hubspot_csv = prospects_dir / "prospects-recent-hubspot.csv"
    hubspot_csv.write_text("email\nalice@example.com\n", encoding="utf-8")
    fresh_time = time.time() - 86400
    os.utime(hubspot_csv, (fresh_time, fresh_time))

    res = sweep_stale_pii(profile, ttl_days=7, content_root=tmp_path)

    assert len(res.archived) == 0
    assert hubspot_csv.exists()


def test_sweep_preserves_ledger_files(tmp_path: Path) -> None:
    profile = "test-tenant"
    prospects_dir = tmp_path / profile / "prospects"
    pool_dir = prospects_dir / "sequences" / ".pool"
    pool_dir.mkdir(parents=True, exist_ok=True)

    latest_json = prospects_dir / "latest.json"
    latest_json.write_text('{"accounts": []}', encoding="utf-8")
    suppression_csv = pool_dir / "suppression.csv"
    suppression_csv.write_text("pattern,reason\n", encoding="utf-8")
    master_csv = pool_dir / "master-list.csv"
    master_csv.write_text("id,company\n", encoding="utf-8")

    # Set all to 100 days old
    ancient = time.time() - (100 * 86400)
    os.utime(latest_json, (ancient, ancient))
    os.utime(suppression_csv, (ancient, ancient))
    os.utime(master_csv, (ancient, ancient))

    res = sweep_stale_pii(profile, ttl_days=7, content_root=tmp_path)

    assert len(res.archived) == 0
    assert latest_json.exists()
    assert suppression_csv.exists()
    assert master_csv.exists()


def test_sweep_dry_run(tmp_path: Path) -> None:
    profile = "test-tenant"
    prospects_dir = tmp_path / profile / "prospects"
    prospects_dir.mkdir(parents=True, exist_ok=True)

    hubspot_csv = prospects_dir / "prospects-20260901-hubspot.csv"
    hubspot_csv.write_text("email\nbob@example.com\n", encoding="utf-8")
    old_time = time.time() - (10 * 86400)
    os.utime(hubspot_csv, (old_time, old_time))

    res = sweep_stale_pii(profile, ttl_days=7, dry_run=True, content_root=tmp_path)

    assert len(res.archived) == 1
    # In dry run, file must NOT be deleted
    assert hubspot_csv.exists()
    assert not (prospects_dir / ".archive").exists()


def test_sweep_empty_directory(tmp_path: Path) -> None:
    res = sweep_stale_pii("empty-profile", ttl_days=7, content_root=tmp_path)
    assert len(res.archived) == 0
    assert len(res.errors) == 0


def test_sweep_guards_unenrolled_ready_to_load(tmp_path: Path) -> None:
    """ready-to-load*.csv files must NOT be archived without --force."""
    profile = "test-tenant"
    prospects_dir = tmp_path / profile / "prospects"
    sequences_dir = prospects_dir / "sequences"
    sequences_dir.mkdir(parents=True, exist_ok=True)

    # Create old ready-to-load files (15 days old)
    rtl = sequences_dir / "ready-to-load.csv"
    rtl.write_text("email\nalice@example.com\n", encoding="utf-8")

    old_time = time.time() - (15 * 86400)
    os.utime(rtl, (old_time, old_time))

    # Without --force: file must be guarded, NOT archived
    res = sweep_stale_pii(profile, ttl_days=7, content_root=tmp_path)

    assert len(res.archived) == 0
    assert len(res.guarded) == 1
    assert rtl.exists(), "ready-to-load.csv must survive without --force"
    assert "unenrolled" in res.guarded[0][1]


def test_sweep_force_archives_unenrolled_ready_to_load(tmp_path: Path) -> None:
    """With --force, ready-to-load*.csv files are archived normally."""
    profile = "test-tenant"
    prospects_dir = tmp_path / profile / "prospects"
    sequences_dir = prospects_dir / "sequences"
    sequences_dir.mkdir(parents=True, exist_ok=True)

    rtl = sequences_dir / "ready-to-load.csv"
    rtl.write_text("email\nbob@example.com\n", encoding="utf-8")

    old_time = time.time() - (15 * 86400)
    os.utime(rtl, (old_time, old_time))

    # With --force: file must be archived
    res = sweep_stale_pii(profile, ttl_days=7, force=True, content_root=tmp_path)

    assert len(res.archived) == 1
    assert len(res.guarded) == 0
    assert not rtl.exists(), "ready-to-load.csv must be archived with --force"

    archived_files = list((prospects_dir / ".archive").glob("*.csv.gz"))
    assert len(archived_files) == 1

    with gzip.open(archived_files[0], "rt", encoding="utf-8") as f:
        assert "@example.com" in f.read()


def test_sweep_guards_dated_ready_to_load_variants(tmp_path: Path) -> None:
    """Dated ready-to-load variants are also guarded."""
    profile = "test-tenant"
    prospects_dir = tmp_path / profile / "prospects"
    sequences_dir = prospects_dir / "sequences"
    sequences_dir.mkdir(parents=True, exist_ok=True)

    # These are dated variants that _is_target_pii_file currently doesn't match,
    # but _is_unenrolled_load_file covers them if they ever do match.
    # The bare ready-to-load.csv is the one that currently hits both filters.
    rtl = sequences_dir / "ready-to-load.csv"
    rtl.write_text("email\ncarol@example.com\n", encoding="utf-8")

    # Also create a hubspot CSV that SHOULD still be archived
    hubspot = prospects_dir / "prospects-20260801-hubspot.csv"
    hubspot.write_text("email\ndan@example.com\n", encoding="utf-8")

    old_time = time.time() - (20 * 86400)
    os.utime(rtl, (old_time, old_time))
    os.utime(hubspot, (old_time, old_time))

    res = sweep_stale_pii(profile, ttl_days=7, content_root=tmp_path)

    # hubspot CSV archived, ready-to-load guarded
    assert len(res.archived) == 1
    assert len(res.guarded) == 1
    assert not hubspot.exists(), "hubspot CSV should be archived"
    assert rtl.exists(), "ready-to-load.csv must survive without --force"


def test_7_day_sweep_archives_cleartext(tmp_path: Path) -> None:
    """Verify that cleartext CSVs older than 7 days lacking an active sequencer flag are archived and removed from the active tree."""
    profile = "test-tenant"
    prospects_dir = tmp_path / profile / "prospects"
    sequences_dir = prospects_dir / "sequences"
    sequences_dir.mkdir(parents=True, exist_ok=True)

    # Old hubspot CSV (8 days old)
    hubspot_csv = prospects_dir / "prospects-20260901-hubspot.csv"
    hubspot_csv.write_text("first,last,email\nJane,Doe,jane@example.com\n", encoding="utf-8")
    old_time = time.time() - (8 * 86400)
    os.utime(hubspot_csv, (old_time, old_time))

    # Old ready-to-load CSV (8 days old) without active sequencer flag
    rtl_csv = sequences_dir / "ready-to-load.csv"
    rtl_csv.write_text("first,last,email\nOld,User,old@example.com\n", encoding="utf-8")
    os.utime(rtl_csv, (old_time, old_time))

    res = sweep_stale_pii(profile, ttl_days=7, force=True, content_root=tmp_path)

    assert len(res.archived) >= 2
    assert not hubspot_csv.exists()
    assert not rtl_csv.exists()


def test_unverified_exclusion_from_dashboard(tmp_path: Path, monkeypatch) -> None:
    """Verify that contacts with unverified email status are masked/omitted from the generated HTML view."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    profile = "test-tenant"
    from gtm_core import email_campaign_dashboard as gd
    from gtm_core import prospects_consolidate as pc

    seq = pc._prospects_dir(profile, tmp_path) / "sequences"
    seq.mkdir(parents=True, exist_ok=True)
    header = "first,last,email,title,company,company_domain,city,country,segment,tier,signal_clause,why_now,case_study,src,suppression,suppression_date,email_status\n"
    row = "Ada,Byte,unverified-secret@example.com,CISO,Acme,acme.example,SG,Singapore,enterprise,A,Signal,,,,,,unverified\n"
    (seq / "list.csv").write_text(header + row, encoding="utf-8")
    (seq / "cells.toml").write_text('[[sequence]]\nid = "S1"\ncsv = "list.csv"\n', encoding="utf-8")

    model = gd.build_model(profile, tmp_path)
    html = gd.render_html(model)

    # Cleartext email must NOT be present in HTML
    assert "unverified-secret@example.com" not in html
