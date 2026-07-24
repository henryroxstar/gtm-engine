"""Tests for gtm_core.prospects_dashboard — the self-refreshing status page."""

from __future__ import annotations

import csv
import json

from gtm_core import prospects_consolidate as pc
from gtm_core import prospects_dashboard as pd


def _write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _seed(tmp_path, profile="acme"):
    pdir = tmp_path / profile / "prospects"
    # One high-confidence (ready) and one no-signal (needs-verification) person.
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["First Name", "Last Name", "Email", "Company Name", "Company Domain Name", "Email Status"],
        [
            [
                "Ada",
                "Lovelace",
                "ada@analytical.com",
                "Analytical Engine",
                "analytical.com",
                "RocketReach A",
            ],
            ["Grace", "Hopper", "grace@compiler.com", "Compiler Inc", "compiler.com", ""],
        ],
    )
    # Account layer: two companies with no email; one already has a person (Ada).
    _write_csv(
        pdir / "imports" / "bulk-net-new.csv",
        ["business_name", "business_website", "business_domain"],
        [
            ["Analytical Engine", "https://analytical.com", "analytical.com"],  # enriched
            ["Backlog Corp", "https://backlogco.io", "backlogco.io"],  # backlog
        ],
    )
    return pdir


def test_status_model_counts_both_layers(tmp_path):
    profile = "acme"
    _seed(tmp_path, profile)
    pc.consolidate(profile, content_root=tmp_path)

    status = pd.build_status(profile, content_root=tmp_path)
    f = status["funnel"]
    a = status["accounts"]

    assert f["master_total"] == 2
    assert f["ready"] == 1  # Ada, RocketReach A
    assert f["needs_verification"] == 1  # Grace, no signal

    assert a["unique_accounts"] == 2
    assert a["enriched_accounts"] == 1  # Analytical Engine has Ada
    assert a["backlog_accounts"] == 1  # Backlog Corp has no person yet


def test_render_writes_html_and_json_with_real_numbers(tmp_path):
    profile = "acme"
    _seed(tmp_path, profile)
    pc.consolidate(profile, content_root=tmp_path)  # auto-refreshes the dashboard

    html_path = pd.dashboard_path(profile, content_root=tmp_path)
    json_path = pc._pool_dir(profile, content_root=tmp_path) / "status.json"
    assert html_path.exists()
    assert json_path.exists()

    html = html_path.read_text(encoding="utf-8")
    assert "Prospect data status" in html
    assert "Backlog Corp".lower() not in html.lower()  # names aren't leaked into the page
    assert ">1<" in html or "ready to send" in html  # the ready number renders

    model = json.loads(json_path.read_text(encoding="utf-8"))
    assert model["funnel"]["ready"] == 1
    assert model["accounts"]["backlog_accounts"] == 1


def test_normalize_raw_saleshandy_payload():
    raw = {
        "sequenceId": "seq1",
        "sequenceName": "Test",
        "status": "active",
        "prospects": [
            {
                "total": "30",
                "contacted": "20",
                "upcoming": "8",
                "waiting": "2",
                "open": "12",
                "replied": "3",
                "meetingBooked": "1",
                "interested": "2",
                "meetingBookedDealValue": "5000",
                "interestedDealValue": "1000",
            }
        ],
        "emails": {
            "status": {
                "delivered": "19",
                "opened": "12",
                "replied": "3",
                "hardBounced": "1",
                "softBounced": "0",
                "blockBounced": "0",
            }
        },
    }
    n = pd._normalize_seq(raw)
    assert n["loaded"] == 30
    assert n["sent"] == 20
    assert n["pending"] == 10  # upcoming 8 + waiting 2
    assert n["delivered"] == 19
    assert n["replied"] == 3
    assert n["bounced"] == 1  # hard 1 + soft 0 + block 0
    assert n["meetings"] == 1
    assert n["deal_value"] == 6000  # 5000 + 1000


def test_sequence_stats_json_powers_performance_card(tmp_path):
    profile = "acme"
    _seed(tmp_path, profile)
    pc.consolidate(profile, content_root=tmp_path)

    stats = {
        "fetched": "2026-07-23",
        "sequences": [
            {
                "sequenceId": "seqX",
                "sequenceName": "Live One",
                "status": "active",
                "prospects": [
                    {
                        "total": "10",
                        "contacted": "10",
                        "open": "5",
                        "replied": "2",
                        "meetingBooked": "1",
                    }
                ],
                "emails": {"status": {"delivered": "10", "opened": "5", "replied": "2"}},
            }
        ],
    }
    (pc._pool_dir(profile, content_root=tmp_path) / "sequence-stats.json").write_text(
        json.dumps(stats), encoding="utf-8"
    )
    status = pd.build_status(profile, content_root=tmp_path)
    assert len(status["sequences"]) == 1
    assert status["sequences"][0]["replied"] == 2
    assert status["sequences"][0]["meetings"] == 1

    html = pd.render_html(status)
    assert "Live sequencer performance" in html
    assert "reply rate" in html


def test_sequence_state_is_optional(tmp_path):
    """The page renders whether or not the skill layer dropped sequence-state.json."""
    profile = "acme"
    _seed(tmp_path, profile)
    pc.consolidate(profile, content_root=tmp_path)
    status = pd.build_status(profile, content_root=tmp_path)
    assert status["sequence"] is None  # no file dropped -> graceful absence

    seq_file = pc._pool_dir(profile, content_root=tmp_path) / "sequence-state.json"
    seq_file.parent.mkdir(parents=True, exist_ok=True)
    seq_file.write_text(
        json.dumps({"id": "abc", "status": "paused", "loaded": 5}), encoding="utf-8"
    )
    status = pd.build_status(profile, content_root=tmp_path)
    assert status["sequence"]["loaded"] == 5
