"""Unit tests for gtm_core.retention_sweep covering aged dossiers and latest.json purges."""

from __future__ import annotations

import gzip
import json
import os
import time
from pathlib import Path

from gtm_core.retention_sweep import main, sweep_stale_pii


def test_sweep_archives_aged_account_dossiers(tmp_path: Path) -> None:
    profile = "test-tenant"
    accounts_dir = tmp_path / profile / "accounts"
    acme_dir = accounts_dir / "acme-corp"
    acme_dir.mkdir(parents=True, exist_ok=True)

    dossier_file = acme_dir / "dossier-acme-corp-2026-06-01.md"
    dossier_file.write_text("# Acme Corp Dossier\nSensitive PII notes\n", encoding="utf-8")

    # Set mtime to 70 days ago (> 60 days)
    old_time = time.time() - (70 * 86400)
    os.utime(dossier_file, (old_time, old_time))

    res = sweep_stale_pii(profile, dossier_ttl_days=60, content_root=tmp_path)

    assert len(res.archived_dossiers) == 1
    assert not dossier_file.exists()
    # Empty account directory pruned
    assert not acme_dir.exists()

    # Check archive
    archive_dir = tmp_path / profile / "prospects" / ".archive" / "accounts" / "acme-corp"
    assert archive_dir.exists()
    archived_files = list(archive_dir.glob("*.gz"))
    assert len(archived_files) == 1

    with gzip.open(archived_files[0], "rt", encoding="utf-8") as f:
        content = f.read()
        assert "Sensitive PII notes" in content


def test_sweep_preserves_fresh_account_dossiers(tmp_path: Path) -> None:
    profile = "test-tenant"
    accounts_dir = tmp_path / profile / "accounts"
    beta_dir = accounts_dir / "beta-tech"
    beta_dir.mkdir(parents=True, exist_ok=True)

    dossier_file = beta_dir / "dossier-beta-tech-2026-09-15.md"
    dossier_file.write_text("# Beta Tech Dossier\n", encoding="utf-8")

    # Set mtime to 5 days ago (< 60 days)
    fresh_time = time.time() - (5 * 86400)
    os.utime(dossier_file, (fresh_time, fresh_time))

    res = sweep_stale_pii(profile, dossier_ttl_days=60, content_root=tmp_path)

    assert len(res.archived_dossiers) == 0
    assert dossier_file.exists()
    assert beta_dir.exists()


def test_sweep_purges_disqualified_stale_records_from_latest_json(tmp_path: Path) -> None:
    profile = "test-tenant"
    prospects_dir = tmp_path / profile / "prospects"
    prospects_dir.mkdir(parents=True, exist_ok=True)

    latest_file = prospects_dir / "latest.json"
    items = [
        {
            "id": "stale-disqualified",
            "company": "Stale Disqualified Inc",
            "status": "disqualified",
            "signal_observed": "2026-06-01",  # > 60 days old
        },
        {
            "id": "fresh-disqualified",
            "company": "Fresh Disqualified Co",
            "status": "disqualified",
            "signal_observed": "2026-09-18",  # fresh (< 60 days)
        },
        {
            "id": "active-customer",
            "company": "Loyal Customer LLC",
            "status": "customer",
            "signal_observed": "2026-05-01",  # > 60 days but active customer
        },
    ]
    latest_file.write_text(
        json.dumps({"kind": "prospects", "profile": profile, "items": items}),
        encoding="utf-8",
    )

    res = sweep_stale_pii(profile, dossier_ttl_days=60, content_root=tmp_path)

    assert "Stale Disqualified Inc" in res.purged_accounts
    assert len(res.purged_accounts) == 1

    # Verify latest.json retained the other two
    updated = json.loads(latest_file.read_text(encoding="utf-8"))
    kept_ids = {it["id"] for it in updated["items"]}
    assert kept_ids == {"fresh-disqualified", "active-customer"}

    # Verify snapshot was created
    snap_dir = prospects_dir / ".snapshots"
    assert snap_dir.exists()
    assert len(list(snap_dir.glob("latest-*.json"))) == 1


def test_sweep_dry_run_preserves_dossiers_and_ledger(tmp_path: Path) -> None:
    profile = "test-tenant"
    accounts_dir = tmp_path / profile / "accounts"
    acme_dir = accounts_dir / "acme-corp"
    acme_dir.mkdir(parents=True, exist_ok=True)

    dossier_file = acme_dir / "dossier-acme-corp.md"
    dossier_file.write_text("# Content\n", encoding="utf-8")
    old_time = time.time() - (80 * 86400)
    os.utime(dossier_file, (old_time, old_time))

    prospects_dir = tmp_path / profile / "prospects"
    prospects_dir.mkdir(parents=True, exist_ok=True)
    latest_file = prospects_dir / "latest.json"
    latest_file.write_text(
        json.dumps(
            {
                "kind": "prospects",
                "profile": profile,
                "items": [
                    {
                        "id": "aged-out",
                        "company": "Aged Out Co",
                        "status": "do-not-contact",
                        "signal_observed": "2026-05-01",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    res = sweep_stale_pii(profile, dossier_ttl_days=60, dry_run=True, content_root=tmp_path)

    assert len(res.archived_dossiers) == 1
    assert len(res.purged_accounts) == 1

    # In dry-run, file and ledger must NOT be modified
    assert dossier_file.exists()
    updated = json.loads(latest_file.read_text(encoding="utf-8"))
    assert len(updated["items"]) == 1


def test_cli_execution_with_dossier_sweep(tmp_path: Path) -> None:
    profile = "test-cli"
    p_dir = tmp_path / profile / "prospects"
    p_dir.mkdir(parents=True, exist_ok=True)

    code = main(["--profile", profile, "--dossier-ttl-days", "60", "--dry-run"])
    assert code == 0


def test_sweep_purges_undated_records_using_added_at(tmp_path: Path) -> None:
    profile = "test-added-at"
    prospects_dir = tmp_path / profile / "prospects"
    prospects_dir.mkdir(parents=True, exist_ok=True)

    latest_file = prospects_dir / "latest.json"
    items = [
        {
            "id": "old-added",
            "company": "Old Undated Co",
            "status": "new",
            "added_at": "2026-06-01T12:00:00Z",  # > 60 days old, no signal_observed
        },
        {
            "id": "fresh-added",
            "company": "Fresh Undated Co",
            "status": "new",
            "added_at": "2026-09-18T12:00:00Z",  # fresh (< 60 days), no signal_observed
        },
    ]
    latest_file.write_text(
        json.dumps({"kind": "prospects", "profile": profile, "items": items}),
        encoding="utf-8",
    )

    res = sweep_stale_pii(profile, dossier_ttl_days=60, content_root=tmp_path)
    assert "Old Undated Co" in res.purged_accounts
    assert len(res.purged_accounts) == 1

    updated = json.loads(latest_file.read_text(encoding="utf-8"))
    kept_ids = {it["id"] for it in updated["items"]}
    assert kept_ids == {"fresh-added"}


def test_sweep_preserves_replied_status(tmp_path: Path) -> None:
    profile = "test-replied"
    prospects_dir = tmp_path / profile / "prospects"
    prospects_dir.mkdir(parents=True, exist_ok=True)

    latest_file = prospects_dir / "latest.json"
    items = [
        {
            "id": "replied-long-deal",
            "company": "Engaged Reply Co",
            "status": "replied",
            "added_at": "2025-01-01T00:00:00Z",  # > 1 year old
        }
    ]
    latest_file.write_text(
        json.dumps({"kind": "prospects", "profile": profile, "items": items}),
        encoding="utf-8",
    )

    res = sweep_stale_pii(profile, dossier_ttl_days=60, content_root=tmp_path)
    assert len(res.purged_accounts) == 0

    updated = json.loads(latest_file.read_text(encoding="utf-8"))
    assert len(updated["items"]) == 1
    assert updated["items"][0]["id"] == "replied-long-deal"
