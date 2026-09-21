"""Tests for email campaign dashboard generation (Command Center UX)."""

from __future__ import annotations

import json
from pathlib import Path

from gtm_core import email_campaign_dashboard as gd


def test_dashboard_flags_human_gates(tmp_path: Path, monkeypatch) -> None:
    """Inject accounts into 'hold' status and assert generated HTML contains [ACTION REQUIRED] banner and count."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    profile = "test-tenant"
    p_dir = tmp_path / profile / "prospects"
    p_dir.mkdir(parents=True, exist_ok=True)
    evals_dir = p_dir / "evals"
    evals_dir.mkdir(parents=True, exist_ok=True)
    seq_dir = p_dir / "sequences"
    seq_dir.mkdir(parents=True, exist_ok=True)

    # 5 accounts in hold
    held_records = [
        {"email": f"lead{i}@example.com", "lane": "hold", "reason": "tier-a-generic"}
        for i in range(5)
    ]
    (evals_dir / "lanes-state.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in held_records), encoding="utf-8"
    )

    # Latest items
    latest_items = [{"company": f"Company {i}", "stage": "held"} for i in range(5)]
    (p_dir / "latest.json").write_text(
        json.dumps({"kind": "prospects", "items": latest_items}), encoding="utf-8"
    )

    # Minimal sequence setup
    (seq_dir / "cells.toml").write_text("", encoding="utf-8")

    model = gd.build_model(profile, content_root=tmp_path)
    html = gd.render_html(model)

    # Assert [ACTION REQUIRED] CSS alert banner is present
    assert "ACTION REQUIRED" in html
    assert "5" in html
    # Assert review sheet link is present
    assert "review" in html.lower() or "sheet" in html.lower() or "lanes-hold-sheet" in html.lower()


def test_dashboard_safe_download_deliverables(tmp_path: Path, monkeypatch) -> None:
    """Dashboard includes Safe-Download Deliverables section with current ready-to-load files."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    profile = "test-tenant"
    p_dir = tmp_path / profile / "prospects"
    seq_dir = p_dir / "sequences"
    seq_dir.mkdir(parents=True, exist_ok=True)

    # Create fresh ready-to-load.csv
    rtl = seq_dir / "ready-to-load.csv"
    rtl.write_text("first,last,email\nAda,Byte,ada@example.com\n", encoding="utf-8")

    model = gd.build_model(profile, content_root=tmp_path)
    html = gd.render_html(model)

    # Safe downloads section
    assert "Safe-Download Deliverables" in html
    assert "ready-to-load.csv" in html
    assert 'href="prospects/sequences/ready-to-load.csv"' in html


def test_dashboard_visual_attrition_funnel(tmp_path: Path, monkeypatch) -> None:
    """Dashboard includes visual Attrition Funnel block."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    profile = "test-tenant"
    p_dir = tmp_path / profile / "prospects"
    p_dir.mkdir(parents=True, exist_ok=True)

    latest_items = [
        {"company": "Acme Corp", "stage": "ready"},
        {"company": "Beta Inc", "stage": "held"},
    ]
    (p_dir / "latest.json").write_text(
        json.dumps({"kind": "prospects", "items": latest_items}), encoding="utf-8"
    )

    model = gd.build_model(profile, content_root=tmp_path)
    html = gd.render_html(model)

    assert "Attrition Funnel" in html or "Attrition Receipt" in html


def test_dashboard_masks_stale_downloads_and_internal_folders(tmp_path: Path, monkeypatch) -> None:
    """Dashboard omits stale CSVs (>7d) and internal pool files from Safe-Download Deliverables."""
    import os
    import time

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    profile = "test-tenant"
    p_dir = tmp_path / profile / "prospects"
    seq_dir = p_dir / "sequences"
    pool_dir = p_dir / "pool"
    seq_dir.mkdir(parents=True, exist_ok=True)
    pool_dir.mkdir(parents=True, exist_ok=True)

    # 1. Stale ready-to-load file (8 days old)
    stale_rtl = seq_dir / "ready-to-load.csv"
    stale_rtl.write_text("first,last,email\nOld,User,old@example.com\n", encoding="utf-8")
    old_mtime = time.time() - (8 * 86400)
    os.utime(stale_rtl, (old_mtime, old_mtime))

    # 2. Internal pool file (should never be linked)
    pool_csv = pool_dir / "pool.csv"
    pool_csv.write_text("first,last,email\nPool,User,pool@example.com\n", encoding="utf-8")

    # 3. Raw export CSV in prospects dir (should never be linked in safe downloads)
    export_csv = p_dir / "prospects-20260901-hubspot.csv"
    export_csv.write_text("first,last,email\nExport,User,export@example.com\n", encoding="utf-8")

    model = gd.build_model(profile, content_root=tmp_path)
    html = gd.render_html(model)

    # Assert safe downloads section does NOT link to stale or internal files
    assert "No current verified CSVs ready for download" in html
    assert 'href="prospects/sequences/ready-to-load.csv"' not in html
    assert "pool.csv" not in html
    assert "prospects-20260901-hubspot.csv" not in html


def test_consolidate_auto_refreshes_dashboard_and_inputs_inventory(
    tmp_path: Path, monkeypatch
) -> None:
    """Consolidation automatically regenerates email_campaign_status.html and keeps .inputs.json fresh."""
    from gtm_core import prospects_consolidate as pc

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    profile = "test-tenant"
    p_dir = tmp_path / profile / "prospects"
    p_dir.mkdir(parents=True, exist_ok=True)

    header = "first,last,email,title,company,company_domain,city,country,segment,tier,signal_clause,why_now,case_study,src,suppression,suppression_date,email_status\n"
    row = "Jane,Doe,jane@example.com,CTO,Acme,acme.example,SF,USA,enterprise,A,Signal,WhyNow,Case,src,,,verified\n"
    (p_dir / "prospects-20260920-hubspot.csv").write_text(header + row, encoding="utf-8")

    # Run consolidate
    res = pc.consolidate(profile, content_root=tmp_path)
    assert res["ready_to_load"] >= 1

    # Verify that email_campaign_status.html was auto-generated
    dashboard_file = tmp_path / profile / "email_campaign_status.html"
    assert dashboard_file.is_file(), (
        "email_campaign_status.html must be automatically generated by consolidate"
    )

    content = dashboard_file.read_text(encoding="utf-8")
    assert "Attrition Funnel" in content
    assert "Safe-Download Deliverables" in content

    # Verify that .inputs.json was written and page is fresh
    from gtm_core import page_inputs as pi

    inventory_file = pi.inventory_path(dashboard_file)
    assert inventory_file.is_file(), f"inputs inventory must be written at {inventory_file}"

    rep = gd.check_fresh(profile, content_root=tmp_path)
    assert rep.ok, (
        f"Generated dashboard must be fresh immediately after consolidate: {rep.explain()}"
    )

    # Verify that out-of-band edits to inputs are convicted by check_fresh
    (p_dir / "latest.json").write_text(
        '{"kind": "prospects", "items": [{"company": "New Corp"}]}', encoding="utf-8"
    )
    stale_rep = gd.check_fresh(profile, content_root=tmp_path)
    assert not stale_rep.ok, "check_fresh must convict when an input changes out of band"
    assert "latest.json" in stale_rep.explain()

    # Calling render_dashboard directly or via consolidate heals freshness
    gd.render_dashboard(profile, content_root=tmp_path)
    healed_rep = gd.check_fresh(profile, content_root=tmp_path)
    assert healed_rep.ok
