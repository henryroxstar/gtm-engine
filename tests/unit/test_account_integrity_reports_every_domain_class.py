"""`audit_rows` counts and phrases EVERY domain class it can classify, its report prints the
counts it took, and a research-record warning reaches the audit's own warning list.

What the 2026-09-23 hunt found in `gtm_core/account_integrity.py`: the `domain-personal`
and `domain-academic-medical` branches of `audit_rows` — the counter and the sentence an
operator reads — had never been executed by any test (only the classifier underneath them
had); `render()`'s counter block was never asserted, so its numbers could be swapped or
dropped unnoticed; and deleting the line that carries record-tier WARNINGS into the audit
(`a.warnings.extend(rec.warnings)`) survived, because every fixture asserted only the
record-tier ERRORS. The two never-executed `DossierDepth.NONE` returns — the fail-closed
ones — are pinned here too. Fictional fixtures only (§R9).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from gtm_core.account_integrity import (
    DossierDepth,
    audit_rows,
    classify_dossier_folder,
    render,
)
from gtm_core.signal_sources import store_capture

PROFILE = "acme"
AS_OF = date(2026, 8, 19)


def _row(**kw) -> dict:
    base = {
        "first": "Jordan",
        "last": "Vance",
        "email": "jordan.vance@lantern.example",
        "title": "Chief Information Security Officer",
        "company": "Quarry Systems",
        "company_domain": "lantern.example",
        "city": "Austin",
        "country": "United States",
        "segment": "enterprise",
        "tier": "A",
        "signal_clause": "opened an AI agent governance program covering autonomous agents",
        "why_now": "Quarry Systems opened an AI agent governance program in 2026",
        "case_study": "",
        "src": "backlog-enrich",
        "suppression": "",
        "suppression_date": "",
        "signal_source_url": "https://quarrysystems.example/news/ai-governance",
        "signal_observed": "2026-08-01",
        "signal_evidence": (
            "Quarry Systems opened an AI agent governance program covering autonomous "
            "agents across its claims and underwriting workflows."
        ),
        "signal_subject": "Quarry Systems",
        "signal_agent_kind": "ai",
        "category_relation": "prospect",
        "verdict": "send",
        "verdict_reason": "",
    }
    base.update(kw)
    return base


_CAPTURE_TEXT = (
    "Quarry Systems opened an AI agent governance program covering autonomous "
    "agents across its claims and underwriting workflows."
)


def _covered(tmp_path: Path) -> tuple[Path, Path]:
    content = tmp_path / "content"
    folder = content / PROFILE / "accounts" / "quarry-systems"
    folder.mkdir(parents=True)
    (folder / "account-dossier-quarry-systems-2026-08-12.md").write_text("x", encoding="utf-8")
    store_capture(
        "https://quarrysystems.example/news/ai-governance",
        _CAPTURE_TEXT,
        sources_dir=content / "sources",
    )
    store_capture(
        "https://quarrysystems.example/news/ai-governance",
        _CAPTURE_TEXT,
        sources_dir=content / PROFILE / "sources",
    )
    profiles = tmp_path / "profiles"
    return content, profiles


def _audit(rows, content, profiles):
    return audit_rows(rows, PROFILE, content_root=content, profiles_root=profiles, as_of=AS_OF)


def test_a_personal_and_an_academic_medical_address_are_each_counted_and_phrased(tmp_path):
    content, profiles = _covered(tmp_path)
    knowledge = profiles / PROFILE / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "domain-aliases.toml").write_text(
        'schema = 1\n[[alias]]\ndomains = ["riverbend.edu", "lantern.example"]\n', encoding="utf-8"
    )
    rows = [
        _row(email="chris@gmail.com"),
        _row(first="Sam", email="sam.okafor@riverbend.edu"),
    ]
    a = _audit(rows, content, profiles)

    assert (a.domain_personal, a.domain_academic_medical) == (1, 1)
    assert (a.domain_academic, a.domain_mismatch, a.domain_unverifiable) == (0, 0, 0)
    assert not a.errors
    personal = [w for w in a.warnings if w.startswith("domain-personal:")]
    medical = [w for w in a.warnings if w.startswith("domain-academic-medical:")]
    assert personal == [
        "domain-personal: 'chris@gmail.com' at 'Quarry Systems' — free webmail, "
        "confirm this is really the buyer's working address"
    ]
    assert len(medical) == 1
    assert medical[0].startswith(
        "domain-academic-medical: 'sam.okafor@riverbend.edu' at 'Quarry Systems' — "
    )
    assert "confirm the seat is a buyer and not a clinician" in medical[0]

    text = render(a)
    assert "  domain-academic-med:   1\n" in text
    assert "  domain-personal:       1\n" in text
    assert "  domain-academic:       0\n" in text


def test_a_record_tier_warning_reaches_the_audit_not_only_a_record_tier_error(tmp_path):
    content, profiles = _covered(tmp_path)
    a = _audit([_row(category_relation="partner")], content, profiles)
    assert not a.failed
    assert a.warnings == [
        "relation-partner: jordan.vance@lantern.example — 'Quarry Systems' is a partner — a "
        "cold outbound pitch is the wrong motion"
    ]


def test_the_report_prints_every_counter_it_took_and_the_verdict_line(tmp_path):
    content, profiles = _covered(tmp_path)
    clean = _audit([_row()], content, profiles)
    assert render(clean) == (
        "account-integrity audit — 1 row(s), 1 account(s)\n"
        "\n"
        "  no-dossier:            0\n"
        "  domain-academic:       0\n"
        "  domain-academic-med:   0\n"
        "  domain-mismatch:       0\n"
        "  domain-personal:       0\n"
        "  domain-unverifiable:   0  (no company_domain on file)\n"
        "  why-now-not-a-signal:  0\n"
        "  stale-artifact-string: 0\n"
        "  competitor:            0  (0 direct — ERROR)\n"
        "  leadership-freshness:  0\n"
        "\n"
        "  PASS — no account-integrity findings.\n"
        "PASS"
    )
    assert render(clean, pass_text="PASS (2 rows kept)").endswith("\nPASS (2 rows kept)")

    # No dossier behind the row: one error, counted in the block AND listed under the tally.
    empty_content = tmp_path / "empty"
    store_capture(
        "https://quarrysystems.example/news/ai-governance",
        _CAPTURE_TEXT,
        sources_dir=empty_content / "sources",
    )
    bad = _audit([_row()], empty_content, profiles)
    text = render(bad)
    assert "  no-dossier:            1\n" in text
    assert "  ERRORS — 1 finding(s). Do not load until resolved:\n" in text
    assert text.count("\n    - ") == 1
    assert "    - no-dossier: 'Quarry Systems' has no research behind its Why Now clause\n" in text
    assert "PASS" not in text
    assert text.endswith("\nFAIL")


def test_classify_dossier_folder_fails_closed(tmp_path):
    content = tmp_path / "content"
    assert classify_dossier_folder(PROFILE, "", content) == DossierDepth.NONE
    folder = content / PROFILE / "accounts" / "quarry-systems"
    folder.mkdir(parents=True)
    (folder / "notes.txt").write_text("x", encoding="utf-8")
    # A matched folder with no recognised artefact is NONE, never assumed full.
    assert classify_dossier_folder(PROFILE, "quarry-systems", content) == DossierDepth.NONE
    (folder / "account-dossier-quarry-systems-2026-08-12.md").write_text("x", encoding="utf-8")
    assert classify_dossier_folder(PROFILE, "quarry-systems", content) == DossierDepth.FULL
