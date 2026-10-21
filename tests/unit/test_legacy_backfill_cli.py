from __future__ import annotations

import csv
import datetime
from pathlib import Path

from gtm_core.signal_quality import backfill_legacy_csv, main


def test_backfill_legacy_csv_infer_fit_and_virality(tmp_path: Path):
    as_of_date = datetime.date(2026, 9, 1)

    csv_path = tmp_path / "legacy.csv"
    fieldnames = ["email", "company", "title", "why_now", "signal_agent_kind"]
    rows = [
        {
            "email": "alex@vertex.example",
            "company": "Vertex Systems",
            "title": "CTO",
            "why_now": "Announced AI agent governance program on 2026-08-15",
            "signal_agent_kind": "ai",
        },
        {
            "email": "jordan@globex.example",
            "company": "Globex Corp",
            "title": "Director of Security",
            "why_now": "Raised Series B on 2025-01-10",
            "signal_agent_kind": "ai",
        },
        {
            "email": "taylor@acme.example",
            "company": "Acme Corp",
            "title": "VP of Engineering",
            "why_now": "Published tech whitepaper in Aug 2026",
            "signal_agent_kind": "none",
        },
        {
            "email": "morgan@initech.example",
            "company": "Initech",
            "title": "CISO",
            "why_now": "Leading provider of legacy databases",
            "signal_agent_kind": "ai",
        },
        {
            "email": "casey@initech.example",
            "company": "Initech",
            "title": "Head of IT",
            "why_now": "",
            "signal_agent_kind": "",
        },
    ]

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    count = backfill_legacy_csv(csv_path, as_of=as_of_date)
    assert count == 5

    with open(csv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        assert "signal_fit" in reader.fieldnames
        assert "signal_virality" in reader.fieldnames
        out_rows = list(reader)

    # Row 0: agent_kind == "ai" and recent dated why_now -> signal_fit = 2, signal_virality = 1
    assert out_rows[0]["signal_fit"] == "2"
    assert out_rows[0]["signal_virality"] == "1"

    # Row 1: dated why_now, but not recent (2025-01-10 vs 2026-09-01 is > 180d) -> signal_fit = 1, signal_virality = 1
    assert out_rows[1]["signal_fit"] == "1"
    assert out_rows[1]["signal_virality"] == "1"

    # Row 2: dated why_now (Aug 2026) but agent_kind is not "ai" -> signal_fit = 1, signal_virality = 1
    assert out_rows[2]["signal_fit"] == "1"
    assert out_rows[2]["signal_virality"] == "1"

    # Row 3: generic why_now (no dates) -> signal_fit = 0, signal_virality = 1
    assert out_rows[3]["signal_fit"] == "0"
    assert out_rows[3]["signal_virality"] == "1"

    # Row 4: empty why_now -> signal_fit = 0, signal_virality = 1
    assert out_rows[4]["signal_fit"] == "0"
    assert out_rows[4]["signal_virality"] == "1"


def test_backfill_preserves_existing_fit_and_virality(tmp_path: Path):
    as_of_date = datetime.date(2026, 9, 1)
    csv_path = tmp_path / "mixed.csv"
    fieldnames = [
        "email",
        "company",
        "why_now",
        "signal_agent_kind",
        "signal_fit",
        "signal_virality",
    ]
    rows = [
        {
            "email": "alex@vertex.example",
            "company": "Vertex Systems",
            "why_now": "Announced AI agent governance program on 2026-08-15",
            "signal_agent_kind": "ai",
            "signal_fit": "3",
            "signal_virality": "2",
        },
        {
            "email": "jordan@globex.example",
            "company": "Globex Corp",
            "why_now": "Announced AI agent governance program on 2026-08-15",
            "signal_agent_kind": "ai",
            "signal_fit": "",
            "signal_virality": "",
        },
    ]

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    count = backfill_legacy_csv(csv_path, as_of=as_of_date)
    assert count == 1  # Only row 1 was modified

    with open(csv_path, encoding="utf-8", newline="") as f:
        out_rows = list(csv.DictReader(f))

    assert out_rows[0]["signal_fit"] == "3"
    assert out_rows[0]["signal_virality"] == "2"

    assert out_rows[1]["signal_fit"] == "2"
    assert out_rows[1]["signal_virality"] == "1"


def test_backfill_separate_out_path(tmp_path: Path):
    as_of_date = datetime.date(2026, 9, 1)
    src_path = tmp_path / "source.csv"
    out_path = tmp_path / "output.csv"

    fieldnames = ["email", "company", "why_now", "signal_agent_kind"]
    rows = [
        {
            "email": "alex@vertex.example",
            "company": "Vertex Systems",
            "why_now": "2026-08-15",
            "signal_agent_kind": "ai",
        }
    ]
    with open(src_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    count = backfill_legacy_csv(src_path, out_path=out_path, as_of=as_of_date)
    assert count == 1
    assert out_path.is_file()

    # Source should not have signal_fit
    with open(src_path, encoding="utf-8") as f:
        assert "signal_fit" not in f.read()

    # Out should have signal_fit
    with open(out_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert "signal_fit" in reader.fieldnames
        rows = list(reader)
        assert rows[0]["signal_fit"] == "2"
        assert rows[0]["signal_virality"] == "1"


def test_main_cli_backfill_subcommand(tmp_path: Path):
    csv_path = tmp_path / "legacy.csv"
    fieldnames = ["email", "company", "why_now", "signal_agent_kind"]
    rows = [
        {
            "email": "alex@vertex.example",
            "company": "Vertex Systems",
            "why_now": "2026-08-15",
            "signal_agent_kind": "ai",
        }
    ]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    rc = main(["backfill", "--csv", str(csv_path), "--as-of", "2026-09-01"])
    assert rc == 0

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert "signal_fit" in reader.fieldnames
        rows = list(reader)
        assert rows[0]["signal_fit"] == "2"


def test_main_cli_root_shorthand(tmp_path: Path):
    csv_path = tmp_path / "legacy.csv"
    out_path = tmp_path / "out.csv"
    fieldnames = ["email", "company", "why_now", "signal_agent_kind"]
    rows = [
        {
            "email": "alex@vertex.example",
            "company": "Vertex Systems",
            "why_now": "2026-08-15",
            "signal_agent_kind": "ai",
        }
    ]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    rc = main(["--csv", str(csv_path), "--out", str(out_path), "--as-of", "2026-09-01"])
    assert rc == 0
    assert out_path.is_file()


def test_main_cli_missing_csv(capsys):
    rc = main([])
    assert rc == 1


def test_main_cli_invalid_date(tmp_path: Path):
    csv_path = tmp_path / "legacy.csv"
    csv_path.write_text("email,company\nalex@vertex.example,Vertex\n", encoding="utf-8")

    rc = main(["backfill", "--csv", str(csv_path), "--as-of", "not-a-date"])
    assert rc == 2


def test_main_cli_nonexistent_file(tmp_path: Path):
    nonexistent = tmp_path / "does_not_exist.csv"
    rc = main(["backfill", "--csv", str(nonexistent)])
    assert rc == 1
