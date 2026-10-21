"""Unit tests for deterministic cohort builder (gtm_core/source_cohort.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core.confine import ConfinementError
from gtm_core.source_cohort import (
    build_cohort_cells,
    check_ledger_presence,
    load_cohort_members,
    parse_spec_touch,
    sanitize_formula,
)


def test_sanitize_formula():
    assert sanitize_formula("=CMD('calc')") == "'=CMD('calc')"
    assert sanitize_formula("+12345") == "'+12345"
    assert sanitize_formula("-12345") == "'-12345"
    assert sanitize_formula("@SUM(A1)") == "'@SUM(A1)"
    assert sanitize_formula("Normal Company, Inc.") == "Normal Company, Inc."
    assert sanitize_formula("") == ""


def test_parse_spec_touch(tmp_path):
    spec = tmp_path / "spec.md"
    spec.write_text(
        "# Sequence spec\n\n"
        "## 3. Touches\n\n"
        "**Step 1 — Day 0** · Subject: `custom subject line`\n"
        "> Hello {{First Name}},\n"
        ">\n"
        "> {{Why Now}}.\n"
        ">\n"
        "> First paragraph here.\n"
        ">\n"
        "> Second paragraph here.\n"
        ">\n"
        "> Regards\n"
        "> Henry\n",
        encoding="utf-8",
    )
    subj, body = parse_spec_touch(spec)
    assert subj == "custom subject line"
    assert (
        body
        == "<p>Hello {{First Name}},</p><p>{{Why Now}}.</p><p>First paragraph here.</p><p>Second paragraph here.</p><p>Regards<br>Henry</p>"
    )


def test_load_cohort_members_confined_and_formula_safe(tmp_path):
    csv_file = tmp_path / "members.csv"
    csv_file.write_text(
        "slug,evidence_index,name,email,company,title,country,industry,borderline,spec_path\n"
        "test1,Acme,=Alice,alice@example.test,+Acme Corp,Architect,US,Software,false,spec-test1.md\n",
        encoding="utf-8",
    )
    members = load_cohort_members(csv_file, tmp_path)
    assert len(members) == 1
    m = members[0]
    assert m["name"] == "'=Alice"
    assert m["company"] == "'+Acme Corp"
    assert m["email"] == "alice@example.test"
    assert m["borderline"] is False

    # Path outside content root refuses
    with pytest.raises(ConfinementError):
        load_cohort_members(Path("/etc/passwd"), tmp_path)


def test_build_cohort_cells_replaces_sequence_ids_and_fails_on_missing_evidence(tmp_path):
    spec = tmp_path / "spec-test1.md"
    spec.write_text(
        "## 3. Touches\n\n**Step 1** · Subject: Subj\n> Hi,\n>\n> Body\n>\n> Regards\n> Henry\n",
        encoding="utf-8",
    )
    members = [
        {
            "slug": "test1",
            "evidence_index": "Acme",
            "name": "Alice Example",
            "email": "alice@example.test",
            "company": "Acme Corp",
            "title": "Architect",
            "country": "US",
            "industry": "Software",
            "borderline": True,
            "spec_path": "spec-test1.md",
        }
    ]
    evidence = [
        {
            "account": "Acme",
            "signal_source_url": "https://news.example.test",
            "signal_observed": "2026-09-01",
            "signal_evidence": "Quote",
            "why_now": "Why Now",
        }
    ]

    # Normal build with sequence id replacement
    cells, rows = build_cohort_cells(
        members,
        evidence,
        specs_dir=tmp_path,
        content_root=tmp_path,
        sequence_ids={"source-test-test1-2026-10": "seq-real-12345"},
    )
    assert len(cells) == 1
    assert cells[0]["sequence_id"] == "seq-real-12345"
    assert cells[0]["title"].endswith("· BORDERLINE")
    assert rows[0]["signal_source_url"] == "https://news.example.test"

    # Missing evidence raises ValueError
    with pytest.raises(ValueError, match="missing from evidence JSON"):
        build_cohort_cells(
            members,
            [],
            specs_dir=tmp_path,
            content_root=tmp_path,
        )


def test_check_ledger_presence_identifies_unresolved():
    members = [
        {
            "name": "Alice",
            "company": "Acme Corp",
            "email": "alice@acme.example",
            "evidence_index": "Acme",
        },
        {
            "name": "Bob",
            "company": "Beta Inc",
            "email": "bob@beta.example",
            "evidence_index": "Beta",
        },
    ]
    evidence = [
        {"account": "Acme", "domain": "acme.example"},
        {"account": "Beta", "domain": "beta.example"},
    ]
    ledger_items = [
        {"domain": "acme.example", "email": "other@acme.example"},
    ]
    warnings = check_ledger_presence(members, evidence, ledger_items)
    assert any("Alice" in w and "contact is not resolved in ledger" in w for w in warnings)
    assert any("Bob" in w and "not found in ledger" in w for w in warnings)
