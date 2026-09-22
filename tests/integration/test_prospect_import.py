"""Integration tests for gtm_core.prospects_import standard mode (canonical item expansion)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from gtm_core import prospects_import as pi
from gtm_core import prospects_state as ps


def test_build_standard_item_fills_every_canonical_field():
    minimal = {
        "company": "Apex Analytics, Inc.",
        "score": 9,
        "why_now": "Launched MCP gateway for agent governance",
        "signal_evidence": "Apex Analytics announced MCP gateway for agent governance",
        "contact_name": "Jordan Lee",
        "contact_title": "VP of Engineering",
        "domain": "apexanalytics.example",
    }

    full = pi.build_standard_item(minimal)

    for field in pi.CANONICAL_FIELDS:
        assert field in full, f"Missing canonical field: {field}"

    assert full["id"] == "apex-analytics-inc"
    assert full["company"] == "Apex Analytics, Inc."
    assert full["tier"] == "A"
    assert full["score"] == 9
    assert full["status"] == "new"
    assert full["priority"] == "high"
    assert full["heat"] == 0
    assert full["intent_feeds"] == []
    assert full["new_in_role"] is False
    # PSK-012: the four fail-closed record fields are research conclusions. The caller
    # supplied none, so none is invented — `check_record` must still BLOCK on each. The
    # clause even says "agent"; the kind is classified by a researcher, never by substring.
    assert full["signal_subject"] == ""
    assert full["signal_agent_kind"] == ""
    assert full["category_relation"] == ""
    assert full["verdict"] == ""
    assert full["verdict_reason"] == ""
    # `lanes route` is the only thing that stamps a lane.
    assert full["lane"] == ""
    assert full["lane_reason"] == ""
    # Optional fields preserved for HubSpot
    assert full["domain"] == "apexanalytics.example"


def test_build_standard_item_without_why_now_invents_no_verdict():
    """It used to write `re-angle` + "no dated public why-now found this pass" — a
    research finding, stamped by an expander that ran no research pass."""
    minimal = {
        "company": "Quiet Tech Labs",
        "score": 6,
    }

    full = pi.build_standard_item(minimal)

    assert full["id"] == "quiet-tech-labs"
    assert full["tier"] == "B"
    assert full["priority"] == "medium"
    assert full["verdict"] == ""
    assert full["verdict_reason"] == ""
    assert full["lane"] == ""
    assert full["lane_reason"] == ""


def test_stage_standard_cli(tmp_path: Path):
    in_file = tmp_path / "minimal.json"
    out_file = tmp_path / "staged.json"

    data = [
        {"company": "First Co", "score": 8, "why_now": "Production AI agents live"},
        {"company": "Second Co", "score": 5},
    ]
    in_file.write_text(json.dumps(data), encoding="utf-8")

    code = pi._cli(["stage-standard", "--items", str(in_file), "--out", str(out_file)])
    assert code == 0

    staged = json.loads(out_file.read_text(encoding="utf-8"))
    assert len(staged) == 2
    for item in staged:
        for field in pi.CANONICAL_FIELDS:
            assert field in item


def test_finalize_with_standard_mode(tmp_path: Path):
    profile = "test-standard"
    p_dir = tmp_path / profile / "prospects"
    p_dir.mkdir(parents=True, exist_ok=True)

    # Initial state
    latest_file = p_dir / "latest.json"
    latest_file.write_text(
        json.dumps(
            {
                "kind": "prospects",
                "profile": profile,
                "items": [
                    {"id": "existing-co", "company": "Existing Co", "status": "contact-resolved"}
                ],
            }
        ),
        encoding="utf-8",
    )

    minimal_findings = [
        {
            "company": "Delta AI Systems",
            "score": 8,
            "why_now": "Partnered with enterprise on MCP agent tooling",
            "contact_name": "Sam Taylor",
            "contact_email": "sam@deltaai.example",
            "contact_title": "Head of AI",
            "domain": "deltaai.example",
            "market": "United States",
            # A row carrying a SCORE must name the rubric that produced it, or `finalize`
            # refuses before writing anything (`prospects_import.require_rubric_provenance`).
            # "minimal" here is about the research fields, not the provenance.
            "rubric_source": "knowledge/fixture.md#rubric",
            "rubric_version": "2026-01-01",
        }
    ]

    summary = pi.finalize(
        profile=profile,
        scored_items=minimal_findings,
        source_run="run-std-01",
        content_root=tmp_path,
        standard=True,
    )

    # Verify latest.json merged properly
    data = ps.load_latest(profile, content_root=tmp_path)
    assert len(data["items"]) == 2

    # Sticky status on old account preserved
    old = next(i for i in data["items"] if i["id"] == "existing-co")
    assert old["status"] == "contact-resolved"

    # New item has every canonical field
    new_item = next(i for i in data["items"] if i["id"] == "delta-ai-systems")
    for f in pi.CANONICAL_FIELDS:
        assert f in new_item
    assert new_item["status"] == "new"
    assert new_item["tier"] == "A"
    assert new_item["verdict"] == ""  # none supplied, so none recorded (PSK-012)

    # Verify HubSpot CSV
    csv_path = Path(summary["hubspot_csv"])
    assert csv_path.exists()
    rows = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["Company Name"] == "Delta AI Systems"
    assert rows[0]["Email"] == "sam@deltaai.example"
    assert rows[0]["Job Title"] == "Head of AI"
    assert rows[0]["GTM_Score"] == "8"
    assert rows[0]["GTM_Tier"] == "A"
