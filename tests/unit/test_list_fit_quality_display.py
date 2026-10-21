import csv
from pathlib import Path

from gtm_core.list_fit import audit_rows, main, render


def test_list_fit_quality_and_cxo_cross_tabulation(tmp_path: Path) -> None:
    # 50 rows total:
    # 12 Tier 1 (10 CxO, 2 VP)
    # 18 Tier 2 (0 CxO, 18 VP)
    # 15 Tier 3 (15 CxO, will refuse)
    # 5 unverified (5 CxO, missing signal_fit)
    csv_path = tmp_path / "prospects.csv"
    rows = []
    # 10 Tier 1 CxO: fit=3, virality=2, recency <= 7d (1.0)
    for i in range(10):
        rows.append(
            {
                "email": f"cxo_ready_{i}@acme.example",
                "title": "Chief Technology Officer",
                "why_now": "Deployment on 2026-10-01",
                "signal_observed": "2026-10-01",
                "signal_fit": "3",
                "signal_virality": "2",
            }
        )
    # 2 Tier 1 VP
    for i in range(2):
        rows.append(
            {
                "email": f"vp_tier1_{i}@acme.example",
                "title": "VP Engineering",
                "why_now": "Deployment on 2026-10-01",
                "signal_observed": "2026-10-01",
                "signal_fit": "3",
                "signal_virality": "2",
            }
        )
    # 18 Tier 2 VP: fit=2, virality=1, recency <= 30d (0.8)
    for i in range(18):
        rows.append(
            {
                "email": f"vp_tier2_{i}@acme.example",
                "title": "Director of Security",
                "why_now": "Partnership on 2026-09-15",
                "signal_observed": "2026-09-15",
                "signal_fit": "2",
                "signal_virality": "1",
            }
        )
    # 15 Tier 3 CxO: fit=2, virality=0, recency <= 180d (0.3)
    for i in range(15):
        rows.append(
            {
                "email": f"cxo_refuse_{i}@acme.example",
                "title": "Chief Executive Officer",
                "why_now": "Filing on 2026-06-01",
                "signal_observed": "2026-06-01",
                "signal_fit": "2",
                "signal_virality": "0",
            }
        )
    # 5 unverified CxO: missing signal_fit
    for i in range(5):
        rows.append(
            {
                "email": f"cxo_unver_{i}@acme.example",
                "title": "CISO",
                "why_now": "Something without fit",
                "signal_observed": "2026-09-15",
                "signal_fit": "",
                "signal_virality": "1",
            }
        )

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    audit = audit_rows(rows)
    rendered = render(audit)

    assert "signal quality tier (wedge strength):" in rendered
    assert "tier-1 (elite)" in rendered
    assert "tier-2 (strong)" in rendered
    assert "tier-3 (moderate)" in rendered
    assert "cxo signal quality:" in rendered
    assert "tier-1/2 (ready)" in rendered
    assert "10" in rendered
    assert "tier-3/4 (will refuse)" in rendered
    assert "15" in rendered
    assert "unverified (will refuse)" in rendered
    assert "5" in rendered
    assert "(run signal_quality backfill)" in rendered

    # Mixed list with low-tier CxOs emits advisory and does not fail
    ret = main(["--csv", str(csv_path)])
    assert ret == 0


def test_list_fit_all_cxo_none_admissible_blocks(tmp_path: Path) -> None:
    csv_path = tmp_path / "all_cxo_bad.csv"
    rows = [
        {
            "email": "ceo@acme.example",
            "title": "CEO",
            "why_now": "Old news on 2026-01-01",
            "signal_observed": "2026-01-01",
            "signal_fit": "1",
            "signal_virality": "0",
        },
        {
            "email": "cto@acme.example",
            "title": "CTO",
            "why_now": "Unverified why now",
            "signal_observed": "2026-09-01",
            "signal_fit": "",
            "signal_virality": "1",
        },
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    audit = audit_rows(rows)
    assert any("cxo-quality-none-admissible" in f for f in audit.findings)
    assert audit.failed is True

    ret = main(["--csv", str(csv_path)])
    assert ret == 1
