"""Tests for the Hard CxO Gate and row-level signal quality triage in account_integrity.

All fixtures use synthetic, fictional RFC-2606 (.example) data per Rule §R9.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from gtm_core import account_integrity as ai
from gtm_core.prospect_paths import evals_dir
from gtm_core.prospect_readiness import compute_readiness, verify_readiness_conservation
from gtm_core.signal_sources import store_capture

PROFILE = "acme"
AS_OF = "2026-08-14"
AS_OF_DATE = date(2026, 8, 14)


def _write_capture(content_root: Path, url: str, text: str) -> None:
    store_capture(
        url, text, sources_dir=content_root / PROFILE / "sources", profile=PROFILE, tool="test"
    )


def _write_dossier(content_root: Path, slug: str, filename: str) -> None:
    folder = content_root / PROFILE / "accounts" / slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_text("placeholder", encoding="utf-8")


def _write_lane_states(
    content_root: Path, profile: str, recs: list[dict], lane: str = "personalised"
) -> None:
    state_dir = evals_dir(profile, content_root)
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "lanes-state.jsonl").open("w", encoding="utf-8") as fh:
        for r in recs:
            rec = {
                "email": r["email"],
                "lane": lane,
                "trigger": "t1",
                "judge_verdict": "",
                "body_hash": "",
                "stamp": "2026-08-14",
            }
            fh.write(json.dumps(rec) + "\n")


def _row(**kw) -> dict:
    base = {
        "first": "Alex",
        "last": "Mercer",
        "email": "alex@apex.example",
        "title": "Vice President of Engineering",
        "company": "Apex Corp",
        "company_domain": "apex.example",
        "city": "Austin",
        "country": "United States",
        "segment": "enterprise",
        "tier": "A",
        "signal_clause": "deployed enterprise identity gateway",
        "why_now": "Apex Corp deployed enterprise identity gateway in August 2026",
        "case_study": "",
        "src": "backlog-enrich",
        "suppression": "",
        "suppression_date": "",
        "signal_source_url": "https://apex.example/news/identity-gateway",
        "signal_observed": "2026-08-01",
        "signal_evidence": "Apex Corp deployed enterprise identity gateway across microservices.",
        "signal_subject": "Apex Corp",
        "signal_agent_kind": "ai",
        "category_relation": "prospect",
        "verdict": "send",
        "verdict_reason": "fresh signal",
        "signal_fit": "2",
        "signal_virality": "1",
        "judge_verdict": "",
        "judge_calibrated": "",
    }
    base.update(kw)
    return base


def _setup_list(
    tmp_path: Path, monkeypatch, rows: list[dict], lane: str = "personalised"
) -> tuple[Path, Path]:
    content_root = tmp_path / "content"
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content_root))
    csv_dir = content_root / PROFILE / "accounts" / "batch-1"
    csv_dir.mkdir(parents=True, exist_ok=True)
    for r in rows:
        url = r.get("signal_source_url")
        ev = r.get("signal_evidence")
        if url and ev:
            _write_capture(content_root, url, ev)
        comp = r.get("company", "Apex Corp").lower().replace(" ", "-")
        _write_dossier(content_root, comp, "account-dossier-2026-08-01.docx")
    _write_lane_states(content_root, PROFILE, rows, lane=lane)

    cols = list(rows[0].keys())
    csv_path = csv_dir / "list.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return csv_path, content_root


def test_mixed_batch_triage_kept_and_refused_csv(tmp_path, monkeypatch, capsys):
    """Clean VP leads and Tier 1/2 CxOs are kept, while Tier 3/4 and unverified CxOs are triaged."""
    rows = [
        # VP Engineering -> Non-CxO (kept)
        _row(
            email="vp1@apex.example",
            title="Vice President of Engineering",
            signal_observed="2026-08-01",
            signal_fit="2",
        ),
        # Tier 1 CxO -> CTO, 13d old, fit=3, virality=2 -> Tier 1 (kept)
        _row(
            email="cto@apex.example",
            title="Chief Technology Officer",
            signal_observed="2026-08-01",
            signal_fit="3",
            signal_virality="2",
        ),
        # Tier 2 CxO -> CISO, 13d old, fit=2, virality=1 -> Tier 2 (kept)
        _row(
            email="ciso@apex.example",
            title="Chief Information Security Officer",
            signal_observed="2026-08-01",
            signal_fit="2",
            signal_virality="1",
        ),
        # Tier 3 CxO -> CEO, 13d old, fit=1 -> Tier 3 (triaged cxo-signal-quality-low)
        _row(
            email="ceo@apex.example",
            title="Chief Executive Officer",
            signal_observed="2026-08-01",
            signal_fit="1",
            signal_virality="1",
        ),
        # Unverified CxO -> CIO, missing signal_fit -> (triaged cxo-signal-quality-unverified)
        _row(
            email="cio@apex.example",
            title="Chief Information Officer",
            signal_observed="2026-08-01",
            signal_fit="",
        ),
    ]
    csv_path, _ = _setup_list(tmp_path, monkeypatch, rows)
    kept_path = csv_path.parent / "kept.csv"
    refused_path = csv_path.parent / "refused.csv"

    rc = ai.main(
        [
            "--csv",
            str(csv_path),
            "--profile",
            PROFILE,
            "--lane",
            "personalised",
            "--require-verdict",
            "send",
            "--as-of",
            AS_OF,
            "--write-kept",
            str(kept_path),
            "--write-refused",
            str(refused_path),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "GATE 2 AUDIT: 2 CxO recipient(s) triaged from enrollment (Tier 3/4)." in out
    assert f"Surviving 3 leads kept in {kept_path}." in out

    # Check kept.csv
    with kept_path.open(newline="", encoding="utf-8") as fh:
        kept_rows = list(csv.DictReader(fh))
    assert len(kept_rows) == 3
    kept_emails = [r["email"] for r in kept_rows]
    assert kept_emails == ["vp1@apex.example", "cto@apex.example", "ciso@apex.example"]
    for r in kept_rows:
        assert r["signal_quality_tier"] in ("1", "2")
        assert float(r["signal_recency_score"]) > 0.0

    # Check refused.csv
    with refused_path.open(newline="", encoding="utf-8") as fh:
        refused_rows = list(csv.DictReader(fh))
    assert len(refused_rows) == 2
    assert refused_rows[0]["email"] == "ceo@apex.example"
    assert "cxo-signal-quality-low (Signal Tier 3)" in refused_rows[0]["refusal_reason"]
    assert refused_rows[1]["email"] == "cio@apex.example"
    assert "cxo-signal-quality-unverified" in refused_rows[1]["refusal_reason"]


def test_absent_fit_on_cxo_fails_closed_unverified(tmp_path, monkeypatch, capsys):
    """Critical Test: CxO with missing signal_fit is refused with cxo-signal-quality-unverified."""
    rows = [
        _row(email="ceo@apex.example", title="Chief Executive Officer", signal_fit=""),
    ]
    csv_path, _ = _setup_list(tmp_path, monkeypatch, rows)
    refused_path = csv_path.parent / "refused.csv"

    rc = ai.main(
        [
            "--csv",
            str(csv_path),
            "--profile",
            PROFILE,
            "--lane",
            "personalised",
            "--require-verdict",
            "send",
            "--as-of",
            AS_OF,
            "--write-refused",
            str(refused_path),
        ]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "REFUSED: 0 rows survived verdict and quality filters" in err

    with refused_path.open(newline="", encoding="utf-8") as fh:
        refused_rows = list(csv.DictReader(fh))
    assert len(refused_rows) == 1
    assert refused_rows[0]["email"] == "ceo@apex.example"
    assert refused_rows[0]["refusal_reason"] == "cxo-signal-quality-unverified"


def test_all_cxo_list_with_zero_ready_leads_exits_2(tmp_path, monkeypatch, capsys):
    """If an all-CxO list has 0 ready leads, account_integrity exits 2."""
    rows = [
        _row(email="ceo@apex.example", title="CEO", signal_observed="2025-01-01", signal_fit="2"),
        _row(email="cto@apex.example", title="CTO", signal_observed="2025-01-01", signal_fit="1"),
    ]
    csv_path, _ = _setup_list(tmp_path, monkeypatch, rows)
    rc = ai.main(
        [
            "--csv",
            str(csv_path),
            "--profile",
            PROFILE,
            "--lane",
            "personalised",
            "--require-verdict",
            "send",
            "--as-of",
            AS_OF,
        ]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "REFUSED: 0 rows survived verdict and quality filters" in err


def test_strict_flag_exits_2_when_cxo_triaged(tmp_path, monkeypatch, capsys):
    """When --strict is provided, any triaged CxO causes exit code 2 even if other rows survive."""
    rows = [
        _row(email="vp@apex.example", title="Vice President of Sales", signal_fit="2"),
        _row(email="ceo@apex.example", title="CEO", signal_fit=""),
    ]
    csv_path, _ = _setup_list(tmp_path, monkeypatch, rows)
    kept_path = csv_path.parent / "kept.csv"

    rc = ai.main(
        [
            "--csv",
            str(csv_path),
            "--profile",
            PROFILE,
            "--lane",
            "personalised",
            "--require-verdict",
            "send",
            "--as-of",
            AS_OF,
            "--strict",
            "--write-kept",
            str(kept_path),
        ]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "REFUSED (--strict): 1 CxO recipient(s) triaged from enrollment" in err


def test_kill_switch_allows_low_tier_cxo_with_advisory(tmp_path, monkeypatch, capsys):
    """GTM_CXO_SIGNAL_GATE_ENABLED=false allows low-tier CxO through with advisory warning."""
    monkeypatch.setenv("GTM_CXO_SIGNAL_GATE_ENABLED", "false")
    rows = [
        _row(email="ceo@apex.example", title="CEO", signal_observed="2026-08-01", signal_fit="1"),
    ]
    csv_path, _ = _setup_list(tmp_path, monkeypatch, rows, lane="personalised")
    kept_path = csv_path.parent / "kept.csv"

    rc = ai.main(
        [
            "--csv",
            str(csv_path),
            "--profile",
            PROFILE,
            "--lane",
            "personalised",
            "--require-verdict",
            "send",
            "--as-of",
            AS_OF,
            "--write-kept",
            str(kept_path),
            "--warn-only",
        ]
    )
    captured = capsys.readouterr()
    assert "ADVISORY [gate disabled]: CxO ceo@apex.example" in captured.err
    assert rc == 0
    assert kept_path.exists()


def test_write_refused_directory_confinement(tmp_path, monkeypatch, capsys):
    """--write-refused must land beside the list it filters; foreign directories are refused."""
    rows = [_row(email="ceo@apex.example", title="CEO", signal_fit="")]
    csv_path, _ = _setup_list(tmp_path, monkeypatch, rows)
    foreign_dir = tmp_path / "foreign"
    foreign_dir.mkdir(parents=True, exist_ok=True)
    foreign_refused = foreign_dir / "refused.csv"

    rc = ai.main(
        [
            "--csv",
            str(csv_path),
            "--profile",
            PROFILE,
            "--lane",
            "personalised",
            "--require-verdict",
            "send",
            "--write-refused",
            str(foreign_refused),
        ]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "REFUSED: --write-refused" in err
    assert "is not beside the list it filters" in err


def test_readiness_conservation_with_cxo_triage(tmp_path, monkeypatch):
    """Verify that compute_readiness and verify_readiness_conservation pass with triaged CxOs."""
    rows = [
        _row(
            email="vp@apex.example", title="VP of Engineering", lane="personalised", signal_fit="2"
        ),
        _row(
            email="ceo@apex.example",
            title="CEO",
            lane="personalised",
            signal_observed="2025-01-01",
            signal_fit="1",
        ),
        _row(email="cio@apex.example", title="CIO", lane="personalised", signal_fit=""),
    ]
    csv_path, content_root = _setup_list(tmp_path, monkeypatch, rows)
    groups = [("personalised", rows)]
    fieldnames = list(rows[0].keys())

    readiness = compute_readiness(
        PROFILE,
        rows,
        fieldnames,
        groups,
        content_root=content_root,
        as_of=AS_OF_DATE,
    )
    # Row conservation must hold without raising ValueError
    assert verify_readiness_conservation(readiness) is True
    assert readiness["rows"] == 3
    assert readiness["fates"]["cxo_triaged"] == 2  # CEO and CIO triaged
    assert readiness["fates"]["admitted"] == 1  # VP admitted
    assert sum(readiness["fates"].values()) == 3


def test_gate_discriminates_tier_boundary(tmp_path, monkeypatch, capsys):
    """Tier 2 CxO is admitted; Tier 3 CxO is triaged."""
    rows = [
        # Tier 2 CxO: 13d old, fit=2 -> Tier 2
        _row(email="tier2@apex.example", title="CTO", signal_observed="2026-08-01", signal_fit="2"),
        # Tier 3 CxO: 60d old, fit=1 -> Tier 3
        _row(
            email="tier3@apex.example", title="CISO", signal_observed="2026-06-15", signal_fit="1"
        ),
    ]
    csv_path, _ = _setup_list(tmp_path, monkeypatch, rows)
    kept_path = csv_path.parent / "kept.csv"
    refused_path = csv_path.parent / "refused.csv"

    rc = ai.main(
        [
            "--csv",
            str(csv_path),
            "--profile",
            PROFILE,
            "--lane",
            "personalised",
            "--require-verdict",
            "send",
            "--as-of",
            AS_OF,
            "--write-kept",
            str(kept_path),
            "--write-refused",
            str(refused_path),
        ]
    )
    assert rc == 0
    with kept_path.open(newline="", encoding="utf-8") as fh:
        kept_rows = list(csv.DictReader(fh))
    assert len(kept_rows) == 1
    assert kept_rows[0]["email"] == "tier2@apex.example"

    with refused_path.open(newline="", encoding="utf-8") as fh:
        refused_rows = list(csv.DictReader(fh))
    assert len(refused_rows) == 1
    assert refused_rows[0]["email"] == "tier3@apex.example"
