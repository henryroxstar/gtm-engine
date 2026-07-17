"""Tests for gtm_core.outreach_log — the outreach-pack rollup builder.

Covers the contract the ``prospect`` skill relies on after Step 7:
  - parses the standard Tier-A pack header (title/tier/score/persona/subject)
  - falls back gracefully for a pack with a non-standard title and no Tier line
  - escapes markdown-table-breaking "|" characters in free-text fields
  - writes both outreach-log.md and outreach-log.csv under
    content/<profile>/prospects/
  - is tenant-isolated: only reads content/<profile>/accounts/, never another profile
  - rejects a path-traversal profile segment
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core.outreach_log import build_outreach_log, collect_rows


def _pack(dir_: Path, name: str, text: str) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / name).write_text(text, encoding="utf-8")


def _make_content(tmp_path: Path) -> Path:
    root = tmp_path / "content"

    _pack(
        root / "acme" / "accounts" / "nimbus",
        "prospects-tierb-outreach-nimbus.md",
        (
            "# Outreach Pack — Nimbus — 2026-07-01\n\n"
            "**Tier:** B | **Score:** 7/10 | **Segment:** Startup\n"
            "**Primary persona:** Jordan Lee — Chief Product Officer & Co-founder "
            "(jordan.lee@example.com, verified)\n\n"
            "## Touch 1 — LinkedIn DM (give-first)\n> hello\n\n"
            "## Email (touch 1)\n**Subject:** which agent approved it\n> body\n\n"
            "**Touch 2** ...\n**Touch 3** ...\n"
        ),
    )

    # Non-standard title, no Tier line, a "|" inside the persona field to check escaping.
    _pack(
        root / "acme" / "accounts" / "kinsight",
        "prospects-20260702-outreach-kinsight.md",
        (
            "# Kinsight — outreach pack (give-first)\n\n"
            "**Primary persona:** Community / growth lead | secondary contact (to enrich — no verified email)\n\n"
            "## Touch 1 — LinkedIn DM (give-first)\n> hello\n"
        ),
    )

    # A different profile's pack — must never leak into acme's rollup.
    _pack(
        root / "other-profile" / "accounts" / "widgetco",
        "prospects-tierb-outreach-widgetco.md",
        "# Outreach Pack — WidgetCo — 2026-07-01\n\n**Tier:** B\n**Primary persona:** X — Y (z@w.com, verified)\n",
    )

    return root


def test_parses_standard_pack_header(tmp_path):
    root = _make_content(tmp_path)
    rows = collect_rows(root, "acme")
    nimbus = next(r for r in rows if r.account == "Nimbus")
    assert nimbus.draft_date == "2026-07-01"
    assert nimbus.tier == "B"
    assert nimbus.score == "7/10"
    assert nimbus.persona_name == "Jordan Lee"
    assert nimbus.persona_title == "Chief Product Officer & Co-founder"
    assert nimbus.email_address == "jordan.lee@example.com"
    assert nimbus.email_verified == "yes"
    assert nimbus.email_subject == "which agent approved it"
    assert nimbus.channels == "LinkedIn+Email"
    assert nimbus.followup_touches_in_ladder == 2


def test_falls_back_for_nonstandard_title_and_missing_tier(tmp_path):
    root = _make_content(tmp_path)
    rows = collect_rows(root, "acme")
    kinsight = next(r for r in rows if r.account == "Kinsight")
    assert kinsight.draft_date == "2026-07-02"  # recovered from the filename
    assert kinsight.tier == ""
    assert kinsight.channels == "LinkedIn"  # no "## Email" section in this pack


def test_only_reads_the_given_profiles_accounts(tmp_path):
    root = _make_content(tmp_path)
    rows = collect_rows(root, "acme")
    assert {r.account for r in rows} == {"Nimbus", "Kinsight"}


def test_build_writes_md_and_csv_and_escapes_pipes(tmp_path):
    root = _make_content(tmp_path)
    result = build_outreach_log(root, "acme")

    assert result["total"] == 2
    md_path = Path(result["md_path"])
    csv_path = Path(result["csv_path"])
    assert md_path == root / "acme" / "prospects" / "outreach-log.md"
    assert csv_path == root / "acme" / "prospects" / "outreach-log.csv"
    assert md_path.is_file()
    assert csv_path.is_file()

    md = md_path.read_text(encoding="utf-8")
    table_rows = [line for line in md.splitlines() if line.startswith("| 2026")]
    assert len(table_rows) == 2
    for line in table_rows:
        # every table row must have exactly 9 pipes (8 columns); a raw "|" in a
        # field would silently break the table structure.
        assert line.count("|") == 9


def test_rejects_traversal_profile(tmp_path):
    root = _make_content(tmp_path)
    with pytest.raises(ValueError):
        collect_rows(root, "../escape")
