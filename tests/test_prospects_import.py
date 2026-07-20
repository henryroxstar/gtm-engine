"""Tests for gtm_core.prospects_import — the Vibe export-CSV extractor.

Runs entirely offline against a fixture CSV shaped like a real Vibe export (same
field names, score bands, and dedup shape as the Phase 0 spike) but with fully
synthetic companies — none of the rows describe a real business.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from gtm_core import prospects_import as pi
from gtm_core import prospects_state as ps

FIXTURE = Path(__file__).parent / "fixtures" / "vibe-export-sample.csv"


def test_parse_dedups_in_file_and_counts():
    cands = pi.parse_vibe_export(FIXTURE)
    names = [c["company"] for c in cands]
    # 8 rows, one is a Meridian duplicate -> 7 distinct companies
    assert len(cands) == 7
    assert names.count("Meridian Capital Group") == 1


def test_in_file_dedup_keeps_stronger_intent():
    cands = {c["company"]: c for c in pi.parse_vibe_export(FIXTURE)}
    # the duplicate Meridian row carries an agentic-ai score of 90 -> should win over 68
    assert cands["Meridian Capital Group"]["top_intent_score"] == 90


def test_intent_score_parsing_and_heat():
    cands = {c["company"]: c for c in pi.parse_vibe_export(FIXTURE)}
    # Cascade Bancorp Holdings has agentic-ai 86 -> high intent -> heat 2
    usb = cands["Cascade Bancorp Holdings"]
    assert usb["heat"] == 2
    assert usb["intent_feeds"] == ["vibe-topic"]
    assert usb["top_intent_score"] == 86
    # Voyage's max score is 69 -> elevated but NOT heat
    pay = cands["Voyage Commerce Payments Inc."]
    assert pay["heat"] == 0
    assert pay["top_intent_score"] == 69


def test_no_intent_row_is_zero_heat():
    cands = {c["company"]: c for c in pi.parse_vibe_export(FIXTURE)}
    boring = cands["Boring Bank of Nowhere"]
    assert boring["intent_topics"] == []
    assert boring["heat"] == 0


def test_segment_derivation_from_size():
    cands = {c["company"]: c for c in pi.parse_vibe_export(FIXTURE)}
    assert cands["Meridian Capital Group"]["segment"] == "enterprise"  # 10001+
    assert cands["TinyFin Labs"]["segment"] == "startup"  # 51-200


def test_market_titlecased():
    cands = pi.parse_vibe_export(FIXTURE)
    assert all(c["market"] == "United States" for c in cands)


def test_split_name():
    assert pi._split_name("Jamie Rivera") == ("Jamie", "Rivera")
    assert pi._split_name("Jamie Van Der Rivera") == ("Jamie", "Van Der Rivera")
    assert pi._split_name("Cher") == ("Cher", "")
    assert pi._split_name("") == ("", "")
    assert pi._split_name(None) == ("", "")


def test_employees_midpoint():
    assert pi._employees_midpoint("10001+") == "10001"
    assert pi._employees_midpoint("5001-10000") == "7500"
    assert pi._employees_midpoint("51-200") == "125"
    assert pi._employees_midpoint("") == ""
    assert pi._employees_midpoint("n/a") == ""


def test_format_intent_topics():
    topics = [{"topic": "mlops", "score": 72}, {"topic": "agentic ai", "score": 86}]
    assert pi._format_intent_topics(topics) == "agentic ai:86;mlops:72"
    assert pi._format_intent_topics([]) == ""
    assert pi._format_intent_topics(None) == ""
    # topics missing a name are skipped, not written as ":<score>"
    assert pi._format_intent_topics([{"score": 50}]) == ""


def test_dedupe_against_exclude():
    cands = pi.parse_vibe_export(FIXTURE)
    kept, dropped = pi.dedupe_against_exclude(
        cands, {"meridian capital group", "Highbridge Financial Corporation"}
    )
    kept_names = {c["company"] for c in kept}
    assert "Meridian Capital Group" not in kept_names
    assert "Highbridge Financial Corporation" not in kept_names
    assert set(dropped) == {"Meridian Capital Group", "Highbridge Financial Corporation"}


def test_ingest_writes_candidates_and_logs_cost(tmp_path):
    # exclude one company to exercise the exclusion path end-to-end
    exclude_path = tmp_path / "exclude.json"
    exclude_path.write_text(json.dumps({"companies": ["Boring Bank of Nowhere"]}))

    summary = pi.ingest(FIXTURE, "acme", "run-x", exclude_path=exclude_path, content_root=tmp_path)
    assert summary["parsed"] == 7
    assert summary["excluded"] == 1
    assert summary["kept"] == 6
    # high-heat (top score >=75): Meridian (dup row wins @90), Cascade Bancorp Holdings (86),
    # Highbridge (governance 76), TinyFin Labs (81) = 4
    assert summary["high_heat"] == 4

    # candidates file written
    cand_file = Path(summary["candidates_file"])
    assert cand_file.exists()
    data = json.loads(cand_file.read_text())
    assert len(data["candidates"]) == 6

    # cost logged to costs.jsonl (parsed rows, not kept) -> 7 * 2 = 14 credits
    costs = (tmp_path / "acme" / "costs.jsonl").read_text().strip().splitlines()
    rec = json.loads(costs[-1])
    assert rec["tool"] == "vibe-prospecting"
    assert rec["units"]["credits"] == 14


def test_finalize_merges_into_latest_and_writes_hubspot(tmp_path):
    # pre-existing cumulative latest.json with an operator-edited status
    ps_path = ps.latest_path("acme", content_root=tmp_path)
    ps_path.parent.mkdir(parents=True, exist_ok=True)
    ps_path.write_text(
        json.dumps(
            {
                "kind": "prospects",
                "profile": "acme",
                "items": [{"id": "old-co", "company": "Old Co", "status": "contacted"}],
            }
        )
    )

    scored = [
        {
            "id": "cascade-bancorp-holdings",
            "company": "Cascade Bancorp Holdings",
            "segment": "enterprise",
            "market": "United States",
            "tier": "A",
            "score": 8,
            "why_now": "agentic intent 86",
            "qualification_path": "intent-only-relaxed",
            "heat": 2,
            "intent_feeds": ["vibe-topic"],
            "top_intent_score": 86,
            "intent_topics": [
                {"topic": "agentic ai", "score": 86},
                {"topic": "mlops", "score": 72},
            ],
            "industry": "Commercial Banking",
            "revenue_range": "$1B-$10B",
            "contact_name": "Jamie Rivera",
            "contact_email": "jamie.rivera@cascadebancorp.example",
            "contact_title": "Head of AI Platform",
            "domain": "cascadebancorp.example",
            "city": "minneapolis",
            "employees_range": "10001+",
            "priority": "high",
            "status": "new",
        },
    ]
    summary = pi.finalize(
        "acme",
        scored,
        "run-y",
        content_root=tmp_path,
        run_date="2026-07-20",
        rubric_version="2026-05-15",
    )

    # merge-only: old account preserved, new one added
    data = ps.load_latest("acme", content_root=tmp_path)
    companies = {i["company"] for i in data["items"]}
    assert companies == {"Old Co", "Cascade Bancorp Holdings"}
    old = next(i for i in data["items"] if i["company"] == "Old Co")
    assert old["status"] == "contacted"  # sticky preserved through finalize

    # hubspot csv follows the canonical column contract (references/hubspot-csv-map.md),
    # not an ad-hoc shape — this is what makes it a real drop-in HubSpot import.
    hub = Path(summary["hubspot_csv"])
    assert hub.exists()
    rows = list(csv.DictReader(hub.open(newline="", encoding="utf-8")))
    assert len(rows) == 1
    row = rows[0]
    assert row["First Name"] == "Jamie"
    assert row["Last Name"] == "Rivera"
    assert row["Email"] == "jamie.rivera@cascadebancorp.example"
    assert row["Company Name"] == "Cascade Bancorp Holdings"
    assert row["Company Domain Name"] == "cascadebancorp.example"
    assert row["City"] == "minneapolis"
    assert row["Country/Region"] == "United States"
    assert row["Number of Employees"] == "10001"  # midpointed from the "10001+" range
    assert row["GTM_Segment"] == "Enterprise"
    assert row["GTM_Score"] == "8"
    assert row["GTM_Tier"] == "A"
    assert row["GTM_Heat"] == "2"
    assert row["GTM_Intent_Feeds"] == "vibe-topic"
    assert row["GTM_Top_Intent_Score"] == "86"
    assert row["GTM_Intent_Topics"] == "agentic ai:86;mlops:72"
    assert row["GTM_Industry"] == "Commercial Banking"
    assert row["GTM_Revenue_Range"] == "$1B-$10B"
    assert row["GTM_Run_Date"] == "2026-07-20"
    assert row["GTM_Rubric_Version"] == "2026-05-15"
    assert row["GTM_Qualification_Path"] == "intent-only-relaxed"
    assert row["GTM_Source"] == "Cold"  # default when the item doesn't set gtm_source
