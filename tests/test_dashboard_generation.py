"""Tests for email campaign dashboard generation (Command Center UX)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from gtm_core import email_campaign_dashboard as gd


def test_dashboard_flags_human_gates(tmp_path: Path, monkeypatch) -> None:
    """Five CONTACTS waiting on a decision: the [ACTION REQUIRED] banner names that count, in
    contacts — the same figure, in the same words, the terminal block prints."""
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
    assert "<strong>5</strong> contacts are waiting on your decision" in html
    assert "waiting on a routing decision" not in html, "the banner counts contacts, not accounts"


def test_dashboard_safe_download_deliverables(tmp_path: Path, monkeypatch) -> None:
    """With nobody waiting and the list never split, the pooled file IS the one thing to load."""
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

    assert "What to load into the sending tool" in html
    assert "Safe-Download Deliverables" not in html, "three 'safe' files was the defect"
    assert "ready-to-load.csv" in html
    assert 'href="prospects/sequences/ready-to-load.csv"' in html


def test_dashboard_visual_attrition_funnel(tmp_path: Path, monkeypatch) -> None:
    """The accounts card, under the terminal block's own heading and labels."""
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

    assert "Accounts — where each stands" in html
    assert "Attrition" not in html and "Failed Fit" not in html
    # Never sorted, so Held/Ready are the ledger's own words — and the card says so.
    assert "The list has not been sorted yet" in html


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
    assert "Nothing is ready to load yet" in html
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
    assert "Accounts — where each stands" in content
    assert "What to load into the sending tool" in content

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


# --------------------------------------------- the [ACTION REQUIRED] review-sheet link


def _held_profile(tmp_path: Path, profile: str = "test-tenant") -> Path:
    """A profile with accounts in hold, so the ACTION REQUIRED banner renders."""
    p_dir = tmp_path / profile / "prospects"
    (p_dir / "evals").mkdir(parents=True, exist_ok=True)
    (p_dir / "sequences").mkdir(parents=True, exist_ok=True)
    (p_dir / "evals" / "lanes-state.jsonl").write_text(
        "".join(
            json.dumps(
                {"email": f"lead{i}@example.com", "lane": "hold", "reason": "tier-a-generic"}
            )
            + "\n"
            for i in range(5)
        ),
        encoding="utf-8",
    )
    (p_dir / "latest.json").write_text(
        json.dumps(
            {
                "kind": "prospects",
                "items": [{"company": f"Company {i}", "stage": "held"} for i in range(5)],
            }
        ),
        encoding="utf-8",
    )
    (p_dir / "sequences" / "cells.toml").write_text("", encoding="utf-8")
    return p_dir


def _hrefs(html: str) -> list[str]:
    return [
        h for h in re.findall(r'href="([^"#][^"]*)"', html) if not h.startswith(("http", "mailto:"))
    ]


def test_every_link_the_page_emits_resolves_on_disk(tmp_path: Path, monkeypatch) -> None:
    """THE regression. The banner carried a hardcoded `evals/lanes-hold-sheet.csv` that was
    wrong twice: nothing is ever written under that name, and the page sits one directory
    above `prospects/evals/` so the prefix could not resolve either. It shipped because the
    only assertion covering it was `"review" in html.lower()`, which is true of the words in
    the sentence around the link and says nothing about the link.
    """
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    p_dir = _held_profile(tmp_path)
    for stamp in ("2026-09-03", "2026-09-09"):
        (p_dir / "evals" / f"hold-{stamp}.csv").write_text(
            "email,lane\na@b.example,hold\n", encoding="utf-8"
        )
        (p_dir / "evals" / f"hold-{stamp}.html").write_text("<html></html>", encoding="utf-8")

    page = gd.render_dashboard("test-tenant", tmp_path, stubs=False)
    links = _hrefs(page.read_text(encoding="utf-8"))
    assert links, "the banner must offer a link when a sheet exists"
    for href in links:
        assert (page.parent / href).exists(), f"dead link on the rendered page: {href}"


def test_the_banner_links_the_newest_sheet_and_prefers_the_rendered_html(
    tmp_path: Path, monkeypatch
) -> None:
    """Newest by date, and the reviewable `.html` over the `.csv` behind it."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    p_dir = _held_profile(tmp_path)
    for stamp in ("2026-09-03", "2026-09-09"):
        (p_dir / "evals" / f"hold-{stamp}.csv").write_text(
            "email,lane\na@b.example,hold\n", encoding="utf-8"
        )
        (p_dir / "evals" / f"hold-{stamp}.html").write_text("<html></html>", encoding="utf-8")

    html = gd.render_html(gd.build_model("test-tenant", content_root=tmp_path))
    assert "prospects/evals/hold-2026-09-09.html" in html
    assert "hold-2026-09-03" not in html, "an older sheet must not be offered over a newer one"


def test_a_decisions_file_is_never_offered_as_the_sheet_to_fill_in(
    tmp_path: Path, monkeypatch
) -> None:
    """`hold-decisions*` are the operator's ANSWERS and sit in the same directory. Linking
    one as the review sheet would send a reader to a file whose decisions are already made,
    so the sheet pattern is anchored at both ends rather than globbing `hold-*`."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    p_dir = _held_profile(tmp_path)
    (p_dir / "evals" / "hold-decisions.jsonl").write_text("{}\n", encoding="utf-8")
    (p_dir / "evals" / "hold-decisions-2026-09-03-filled.csv").write_text("a\n", encoding="utf-8")

    html = gd.render_html(gd.build_model("test-tenant", content_root=tmp_path))
    assert "hold-decisions" not in html
    assert not _hrefs(html), "with no real sheet on disk there is nothing to link"


def test_no_sheet_on_disk_means_no_link_and_the_command_that_builds_one(
    tmp_path: Path, monkeypatch
) -> None:
    """Negative control for the two tests above: same banner, no sheet. A dead link inside
    an ACTION REQUIRED banner is worse than no link — it reads as "the work is over there"
    and costs a search to learn it is not."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    _held_profile(tmp_path)

    html = gd.render_html(gd.build_model("test-tenant", content_root=tmp_path))
    assert "ACTION REQUIRED" in html
    assert not _hrefs(html), "nothing on disk to link, so the banner must offer no href"
    assert "gtm_core.lanes hold-sheet" in html, "say how to build the missing sheet"


# ------------------------------------------------------- keeping every page up to date


def test_refresh_all_re_renders_the_scoped_pages_the_rollup_refresh_leaves_behind(
    tmp_path: Path, monkeypatch
) -> None:
    """THE staleness regression, with its own negative control.

    The refresh that runs after a consolidation called `render_dashboard` with no scope,
    which is `--scope all` — so `campaign-<slug>.html` and `campaign-open.html` were never
    refreshed by anything. Nothing else refreshes them either: no timer, and the one pack
    prompt that re-renders also names the unscoped command. Measured on a live profile,
    four of five pages were behind the same input.
    """
    from gtm_core.email_campaign_dashboard.render import refresh_all

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    profile = "test-tenant"
    p_dir = _held_profile(tmp_path, profile)
    camps = tmp_path / profile / "plans" / "campaigns"
    camps.mkdir(parents=True, exist_ok=True)
    (camps / "acme.campaign.toml").write_text(
        'slug = "acme"\ntitle = "Acme"\nstatus = "active"\n', encoding="utf-8"
    )

    gd.render_dashboard(profile, tmp_path, stubs=False)
    scoped = gd.render_dashboard(profile, tmp_path, stubs=False, scope="campaign", campaign="acme")

    def stale(page: Path) -> bool:
        from gtm_core.email_campaign_dashboard.config import input_globs
        from gtm_core.page_inputs import verify_inventory

        root, _ = input_globs(profile, tmp_path)
        return not verify_inventory(page, root).ok

    # Move an input both pages read, so both go stale together.
    (p_dir / "latest.json").write_text(
        json.dumps({"kind": "prospects", "items": [{"company": "Later", "stage": "held"}]}),
        encoding="utf-8",
    )
    assert stale(scoped), "fixture must actually make the scoped page stale"

    # NEGATIVE CONTROL: the rollup refresh is exactly what used to run, and it leaves the
    # scoped page behind. Without this the test below would pass for any refresh at all.
    gd.render_dashboard(profile, tmp_path, stubs=False)
    assert stale(scoped), "the unscoped refresh must NOT be what fixes the scoped page"

    written = refresh_all(profile, tmp_path, stubs=False)
    assert scoped in written and not stale(scoped)


def test_refresh_all_skips_a_page_whose_scope_it_cannot_read(tmp_path: Path, monkeypatch) -> None:
    """A page with no inventory is left alone rather than rendered under a guessed scope.
    `campaign-open.html` and a two-slug page are both `campaign-*` on disk, so the filename
    cannot say which mode built one — and rendering the wrong scope over a page replaces one
    campaign's numbers with another's, which is worse than leaving it stale."""
    from gtm_core.email_campaign_dashboard.render import refresh_all

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    profile = "test-tenant"
    _held_profile(tmp_path, profile)
    orphan = tmp_path / profile / "campaign-mystery.html"
    orphan.write_text("<html>built by hand, no inventory</html>", encoding="utf-8")

    written = refresh_all(profile, tmp_path, stubs=False)
    assert orphan not in written
    assert orphan.read_text(encoding="utf-8") == "<html>built by hand, no inventory</html>"
