"""Tests for the pre-load account-integrity gate.

Every fixture here is invented. The defect *shapes* are real ones observed on a live run
(an academic-domain contact, a research-note artifact leaking into `company`, a dossier
variant that skips leadership re-verification), but the identities are fictional per the
third-party-PII rule (docs/RULES.md R9): real people and real companies live only in
`profiles/` and `content/`.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from gtm_core import account_integrity as ai
from gtm_core.account_integrity import (
    CompetitorHit,
    DomainIssue,
    DossierDepth,
    audit_rows,
    competitor_match,
    domain_issue,
    dossier_depth,
    load_competitors,
    load_domain_aliases,
    render,
    stale_artifact_string,
)
from gtm_core.signal_record import RECORD_COLUMNS

PROFILE = "acme"


def _row(**kw):
    base = {
        "first": "Jordan",
        "last": "Vance",
        "email": "jordan.vance@vertex.example",
        "title": "Chief Information Security Officer",
        "company": "Vertex Systems",
        "company_domain": "vertex.example",
        "city": "Austin",
        "country": "United States",
        "segment": "enterprise",
        "tier": "A",
        "signal_clause": "opened an AI governance program covering autonomous agents",
        "why_now": "Vertex Systems opened an AI governance program in 2026",
        "case_study": "",
        "src": "backlog-enrich",
        "suppression": "",
        "suppression_date": "",
        # The research record (gtm_core.signal_record). Present on every fixture row so
        # the default is a list that CAN be checked; the migration shape — a list from
        # before these columns existed — gets its own test below.
        "signal_source_url": "https://vertexsystems.example/news/ai-governance",
        "signal_observed": "2026-08-01",
        "signal_evidence": (
            "Vertex Systems opened an AI governance program covering autonomous agents "
            "across its claims and underwriting workflows."
        ),
        "signal_subject": "Vertex Systems",
        "signal_agent_kind": "ai",
        "category_relation": "prospect",
        "verdict": "send",
        "verdict_reason": "",
    }
    base.update(kw)
    return base


def _write_dossier(tmp_path: Path, slug: str, filename: str) -> None:
    folder = tmp_path / PROFILE / "accounts" / slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_text("placeholder", encoding="utf-8")


# --- dossier depth ---------------------------------------------------------


def test_dossier_depth_none_when_no_folder(tmp_path):
    assert dossier_depth(PROFILE, "Vertex Systems", "vertex.example", tmp_path) == DossierDepth.NONE


def test_dossier_depth_recognizes_the_research_pack_variant(tmp_path):
    """The markdown `dossier-<slug>-<date>.md` shape — added 2026-08-13 after this exact
    glob gap caused a false no-dossier reading on 439 real accounts."""
    _write_dossier(tmp_path, "vertex-systems", "dossier-vertex-systems-2026-08-12.md")
    depth = dossier_depth(PROFILE, "Vertex Systems", "vertex.example", tmp_path)
    assert depth == DossierDepth.BRIEF


def test_dossier_depth_prefers_full_over_brief(tmp_path):
    _write_dossier(tmp_path, "vertex-systems", "prospecting-brief-vertex-systems-2026-08-12.docx")
    _write_dossier(tmp_path, "vertex-systems", "account-dossier-vertex-systems-2026-08-12.docx")
    assert dossier_depth(PROFILE, "Vertex Systems", "vertex.example", tmp_path) == DossierDepth.FULL


def test_dossier_depth_onepager(tmp_path):
    _write_dossier(tmp_path, "vertex-systems", "vertex-systems-ceo-onepager-2026-08-12.docx")
    assert (
        dossier_depth(PROFILE, "Vertex Systems", "vertex.example", tmp_path)
        == DossierDepth.ONEPAGER
    )


# --- domain integrity --------------------------------------------------------


def test_domain_issue_none_when_domains_match():
    issue, _ = domain_issue(_row())
    assert issue == DomainIssue.NONE


def test_domain_issue_none_for_a_subdomain_relationship():
    issue, _ = domain_issue(_row(email="j.vance@mail.vertex.example"))
    assert issue == DomainIssue.NONE


def test_domain_issue_academic_is_a_hard_stop():
    """The adelphi.edu / georgetown.edu / stanford.edu shape found 2026-08-12 — never a
    legitimate corporate contact, unlike a generic domain divergence."""
    issue, detail = domain_issue(
        _row(
            email="j.vance@riverbend.edu", company="Vertex Systems", company_domain="vertex.example"
        )
    )
    assert issue == DomainIssue.ACADEMIC
    assert "riverbend.edu" in detail


def test_domain_issue_generic_mismatch_is_advisory_not_a_hard_stop():
    """A brand-vs-legal-name or parent/subsidiary domain split (Chase/jpmchase.com,
    Merrill Lynch/bofa.com in the real run) is frequently legitimate — escalating every
    mismatch to a hard block was tried first and produced mostly noise."""
    issue, detail = domain_issue(
        _row(
            email="j.vance@parentco.example",
            company="Vertex Systems",
            company_domain="vertex.example",
        )
    )
    assert issue == DomainIssue.MISMATCH
    assert "parentco.example" in detail


def test_domain_issue_personal_webmail():
    issue, _ = domain_issue(_row(email="chris@gmail.com"))
    assert issue == DomainIssue.PERSONAL


def test_domain_issue_unverifiable_when_no_company_domain():
    issue, _ = domain_issue(_row(company_domain=""))
    assert issue == DomainIssue.UNVERIFIABLE


# --- stale artifact strings ---------------------------------------------------


@pytest.mark.parametrize(
    "company",
    [
        "Vertex Systems Parent",
        "Vertex Systems Subsidiary",
        "Vertex Systems Duplicate",
        "Vertex Systems TBD",
        "Vertex Systems (disambiguation from Vertex Holdings)",
        "Vertex Systems (dup)",
    ],
)
def test_stale_artifact_string_catches_research_note_leaks(company):
    assert stale_artifact_string(company)


@pytest.mark.parametrize(
    "company",
    [
        "Vertex Systems",
        "Johnson & Johnson",
        "Martin's Point Health Care",
        "Blue Cross and Blue Shield of Kansas",
        "D.A. Davidson Companies",
    ],
)
def test_stale_artifact_string_leaves_clean_names_alone(company):
    assert stale_artifact_string(company) == ""


# --- competitor flag -----------------------------------------------------------

_COMPETITORS_TOML = """
schema = 1

[[competitor]]
name = "Rival Corp"
tier = "direct"
aliases = ["Rival", "Rival Systems"]
domains = ["rival.example"]
watch_urls = []
syften_filter = ""
first_seen = "2026-08-01"
last_reviewed = "2026-08-01"
status = "active"
note = "Ships an overlapping product in the same space."
"""


def _write_competitors(tmp_path: Path) -> Path:
    root = tmp_path / "profiles"
    (root / PROFILE / "knowledge").mkdir(parents=True)
    (root / PROFILE / "knowledge" / "competitors.toml").write_text(
        _COMPETITORS_TOML, encoding="utf-8"
    )
    return root


def test_load_competitors_empty_when_file_missing(tmp_path):
    assert load_competitors(PROFILE, tmp_path / "profiles") == {}


def test_competitor_match_by_canonical_name(tmp_path):
    root = _write_competitors(tmp_path)
    competitors = load_competitors(PROFILE, root)
    hit = competitor_match("Rival Corp", "", competitors)
    assert hit is not None
    assert hit.tier == "direct" and hit.direct
    assert "Ships an overlapping product" in hit.summary


def test_competitor_match_by_alias_and_domain():
    """The real run's 'Okta Singapore' vs. the watchlist's 'Okta for AI Agents' shape —
    a prospected account is often named by product/region, not the entity the
    watchlist was filed under, so matching must go through aliases AND domains."""
    competitors = {
        "rival": CompetitorHit("direct", "Rival Corp (direct) — Ships an overlapping product."),
    }
    # Matches via domain even though the display name is completely different.
    assert competitor_match("Rival Regional Ops", "rival.example", competitors)
    # Matches via a bare-name token when no domain is on file.
    assert competitor_match("Rival", "", competitors)


def test_competitor_match_none_for_an_unrelated_account(tmp_path):
    root = _write_competitors(tmp_path)
    competitors = load_competitors(PROFILE, root)
    assert competitor_match("Vertex Systems", "vertex.example", competitors) is None


# --- end-to-end audit -----------------------------------------------------------


def test_audit_flags_a_clean_row_as_no_dossier_only(tmp_path):
    content_root = tmp_path / "content"
    a = audit_rows(
        [_row()], PROFILE, content_root=content_root, profiles_root=tmp_path / "profiles"
    )
    assert a.failed
    assert a.no_dossier == 1
    assert a.domain_mismatch == 0
    assert a.competitor == 0


def test_audit_passes_a_fully_covered_row(tmp_path):
    content_root = tmp_path / "content"
    _write_dossier(content_root, "vertex-systems", "account-dossier-vertex-systems-2026-08-12.md")
    a = audit_rows(
        [_row()], PROFILE, content_root=content_root, profiles_root=tmp_path / "profiles"
    )
    assert not a.failed
    assert a.no_dossier == 0
    assert a.leadership_unverified == 0  # full dossier, not brief — no leadership flag


def test_audit_flags_leadership_freshness_for_brief_only_accounts(tmp_path):
    content_root = tmp_path / "content"
    _write_dossier(content_root, "vertex-systems", "dossier-vertex-systems-2026-08-12.md")
    a = audit_rows(
        [_row()], PROFILE, content_root=content_root, profiles_root=tmp_path / "profiles"
    )
    assert not a.failed  # a WARN, not a block
    assert a.leadership_unverified == 1
    # Aggregated, not one finding per account: the dossier variant is chosen once for
    # the whole run, so N identical findings saturate the WARN tier and bury the
    # classes that actually discriminate. See gtm_core.finding_budget.SATURATION.
    lf = [w for w in a.warnings if w.startswith("leadership-freshness")]
    assert len(lf) == 1
    assert "1/1 account(s)" in lf[0]


def test_audit_dedupes_account_level_findings_across_contacts(tmp_path):
    """Two contacts at one under-researched account produce one no-dossier finding,
    not two — account-level checks are deduped by org identity."""
    content_root = tmp_path / "content"
    rows = [
        _row(email="jordan.vance@vertex.example", first="Jordan"),
        _row(email="sam.reyes@vertex.example", first="Sam", last="Reyes"),
    ]
    a = audit_rows(rows, PROFILE, content_root=content_root, profiles_root=tmp_path / "profiles")
    assert a.accounts == 1
    assert a.no_dossier == 1
    assert len([e for e in a.errors if "no-dossier" in e]) == 1


def test_audit_hard_blocks_on_academic_domain_and_stale_artifact(tmp_path):
    content_root = tmp_path / "content"
    _write_dossier(content_root, "vertex-systems", "account-dossier-vertex-systems-2026-08-12.md")
    rows = [
        _row(email="jordan.vance@riverbend.edu"),
        _row(
            email="sam.reyes@vertex.example",
            first="Sam",
            last="Reyes",
            company="Vertex Systems Parent",
        ),
    ]
    a = audit_rows(rows, PROFILE, content_root=content_root, profiles_root=tmp_path / "profiles")
    assert a.failed
    assert a.domain_academic == 1
    assert a.stale_artifact == 1


def test_audit_end_to_end_the_2026_08_12_shape(tmp_path):
    """Regression for the actual gap this module closes: a bulk-sourced account with no
    dossier, an academic-domain contact, and an unflagged competitor all reaching a
    'ready to load' list at once, with invented identities."""
    content_root = tmp_path / "content"
    profiles_root = tmp_path / "profiles"
    _write_competitors(tmp_path)
    # Only one of the three accounts below has a dossier.
    _write_dossier(
        content_root, "steady-industrial", "account-dossier-steady-industrial-2026-08-12.md"
    )

    rows = [
        _row(
            first="Alex",
            last="Prime",
            email="alex.prime@steady-industrial.example",
            company="Steady Industrial",
            company_domain="steady-industrial.example",
        ),
        _row(
            first="Priya",
            last="Chen",
            email="priya.chen@riverbend.edu",
            company="Undossiered Health System",
            company_domain="undossieredhealth.example",
        ),
        _row(
            first="Lee",
            last="Osei",
            email="lee.osei@rival.example",
            company="Rival Corp",
            company_domain="rival.example",
        ),
    ]
    a = audit_rows(rows, PROFILE, content_root=content_root, profiles_root=profiles_root)
    assert a.failed
    assert a.no_dossier == 2  # Undossiered Health System + Rival Corp
    assert a.domain_academic == 1
    assert a.competitor == 1
    # tier="direct" in the fixture: a hard stop since 2026-08-19, not a WARN a human
    # frames. Two direct competitors reached a live sequencer inside a 388-warning
    # block that was acknowledged wholesale.
    assert a.competitor_direct == 1
    assert any("competitor-direct" in e and "Rival Corp" in e for e in a.errors)


# --- the research record + the readability budget ---------------------------


def test_a_pre_record_list_blocks_but_still_runs_every_other_check(tmp_path):
    """The migration shape. One file-level finding for the missing record — and the
    dossier/domain/competitor checks still run, because a migration that also blinds
    them leaves the operator worse off than before the record existed."""
    content_root = tmp_path / "content"
    legacy = {k: v for k, v in _row().items() if k not in RECORD_COLUMNS}
    a = audit_rows(
        [legacy],
        PROFILE,
        content_root=content_root,
        profiles_root=tmp_path / "profiles",
        fieldnames=list(legacy),
    )
    assert a.failed
    assert a.record_missing_columns == list(RECORD_COLUMNS)
    assert a.no_dossier == 1  # the other checks still ran
    out = render(a)
    assert "predates the research record" in out
    assert "no-dossier:" in out


def test_record_findings_reach_the_audit(tmp_path):
    content_root = tmp_path / "content"
    _write_dossier(content_root, "vertex-systems", "account-dossier-vertex-systems-2026-08-12.md")
    a = audit_rows(
        [_row(signal_subject="Northgate Capital")],
        PROFILE,
        content_root=content_root,
        profiles_root=tmp_path / "profiles",
        as_of=date(2026, 8, 19),
    )
    assert a.failed
    assert any("signal-subject-mismatch" in e for e in a.errors)


def test_the_warn_tier_blocks_once_it_stops_being_readable(tmp_path):
    """The 2026-08-19 failure mode, as a regression: a gate that finds the right things
    and prints them in a form nobody reads is a gate that did not run."""
    content_root = tmp_path / "content"
    rows = []
    for i in range(40):
        _write_dossier(content_root, f"acct-{i}", f"account-dossier-acct-{i}-2026-08-12.md")
        rows.append(
            _row(
                email=f"person{i}@parentco.example",  # domain-mismatch on every row
                company=f"Acct {i}",
                company_domain=f"acct-{i}.example",
                signal_clause="",
                signal_source_url="",
                signal_observed="",
                signal_evidence="",
                signal_subject="",
                signal_agent_kind="none",
            )
        )
    a = audit_rows(rows, PROFILE, content_root=content_root, profiles_root=tmp_path / "profiles")
    assert a.warn_verdict.blocked
    assert a.failed
    assert not a.errors  # nothing is individually fatal; the volume is
    assert "Enumeration suppressed" in render(a)

    acked = audit_rows(
        rows,
        PROFILE,
        content_root=content_root,
        profiles_root=tmp_path / "profiles",
        acked=("domain-mismatch",),
    )
    assert not acked.warn_verdict.blocked
    assert not acked.failed


# --- domain aliases (2026-08-21) ---------------------------------------------
#
# `domain-mismatch` is a real check with a real catch (the academic-domain shape), and it
# was also firing on eight known parent/subsidiary pairs across the four re-cut lists —
# every one benign, every one spending warning budget the acting rules need. The pairs are
# now data the profile maintains. These are the negative controls: without the file the
# finding still fires, with it the declared pair goes quiet, and an academic address is
# NEVER rescued by a declaration.


def _write_aliases(profiles_root: Path, body: str) -> None:
    folder = profiles_root / PROFILE / "knowledge"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "domain-aliases.toml").write_text(body, encoding="utf-8")


def test_domain_mismatch_still_fires_with_no_alias_file(tmp_path):
    """Positive control: the check works before any suppression exists."""
    row = _row(email="jordan.vance@parentco.example", company_domain="vertex.example")
    issue, _ = domain_issue(row, load_domain_aliases(PROFILE, tmp_path / "profiles"))
    assert issue == DomainIssue.MISMATCH


def test_a_declared_alias_pair_goes_quiet(tmp_path):
    profiles_root = tmp_path / "profiles"
    _write_aliases(
        profiles_root,
        'schema = 1\n[[alias]]\ndomains = ["parentco.example", "vertex.example"]\nnote = "same org"\n',
    )
    row = _row(email="jordan.vance@parentco.example", company_domain="vertex.example")
    issue, _ = domain_issue(row, load_domain_aliases(PROFILE, profiles_root))
    assert issue == DomainIssue.NONE


def test_alias_pairs_are_unordered(tmp_path):
    """`a -> b` declared must also suppress `b -> a`; a pair is a relationship, not a direction."""
    profiles_root = tmp_path / "profiles"
    _write_aliases(
        profiles_root,
        'schema = 1\n[[alias]]\ndomains = ["vertex.example", "parentco.example"]\nnote = "same org"\n',
    )
    row = _row(email="jordan.vance@parentco.example", company_domain="vertex.example")
    issue, _ = domain_issue(row, load_domain_aliases(PROFILE, profiles_root))
    assert issue == DomainIssue.NONE


def test_an_entry_naming_three_domains_suppresses_every_pairing(tmp_path):
    profiles_root = tmp_path / "profiles"
    _write_aliases(
        profiles_root,
        "schema = 1\n[[alias]]\n"
        'domains = ["a.example", "b.example", "c.example"]\nnote = "one org, three domains"\n',
    )
    aliases = load_domain_aliases(PROFILE, profiles_root)
    assert frozenset(("b.example", "c.example")) in aliases
    assert len(aliases) == 3


def test_an_alias_can_never_rescue_an_academic_address(tmp_path):
    """The one thing this file must not be able to do.

    An `.edu` contact on a corporate account is a hard stop, and a tenant-editable data file
    must not be a route around it. ACADEMIC is classified before the suppression is consulted.
    """
    profiles_root = tmp_path / "profiles"
    _write_aliases(
        profiles_root,
        'schema = 1\n[[alias]]\ndomains = ["riverbend.edu", "vertex.example"]\nnote = "nope"\n',
    )
    row = _row(email="jordan.vance@riverbend.edu", company_domain="vertex.example")
    issue, _ = domain_issue(row, load_domain_aliases(PROFILE, profiles_root))
    assert issue == DomainIssue.ACADEMIC


def test_a_company_domain_carrying_a_url_path_still_matches_an_alias(tmp_path):
    """`company_domain` is sometimes 'kpmg.com/us' — the host is what an alias declares."""
    profiles_root = tmp_path / "profiles"
    _write_aliases(
        profiles_root,
        'schema = 1\n[[alias]]\ndomains = ["parentco.example", "vertex.example"]\nnote = "same org"\n',
    )
    row = _row(email="jordan.vance@parentco.example", company_domain="vertex.example/uk")
    issue, _ = domain_issue(row, load_domain_aliases(PROFILE, profiles_root))
    assert issue == DomainIssue.NONE


# --- tenant guard + scan cost on the dossier lookup (B10) -------------------- #


def test_classify_dossier_folder_guards_the_profile_segment():
    """The canonical accounts-dir helper guards this segment; this path skipped it."""
    with pytest.raises(ValueError):
        ai.classify_dossier_folder("../escape", "some-account")


def test_row_audit_locates_each_account_folder_once(tmp_path, monkeypatch):
    """The fuzzy match re-scans every account folder; doing it twice per row is pure cost."""
    _write_dossier(tmp_path, "northwind", "account-dossier-northwind-2026-08-01.md")
    calls = []
    real = ai.account_has_dossier

    def _counting(*args, **kwargs):
        calls.append(args[:2])
        return real(*args, **kwargs)

    monkeypatch.setattr(ai, "account_has_dossier", _counting)
    ai.audit_rows(
        [_row(company="Northwind", company_domain="northwind.example")],
        profile="acme",
        content_root=tmp_path / "content",
        profiles_root=tmp_path / "profiles",
    )
    assert len(calls) == 1, f"account folder located {len(calls)}x for one account"


# --- the judge ranks; a CALIBRATED judge decides (D1) ------------------------ #


def _verdict_csv(tmp_path, rows):
    p = tmp_path / "list.csv"
    cols = ["email", "company", "company_domain", "verdict", "judge_verdict", "judge_calibrated"]
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    return p


def _run_filter(tmp_path, rows, capsys):
    p = _verdict_csv(tmp_path, rows)
    ai.main(["--csv", str(p), "--profile", "acme", "--require-verdict", "send", "--warn-only"])
    return capsys.readouterr().out


def test_an_uncalibrated_judge_drop_does_not_remove_the_row(tmp_path, capsys):
    """It has never been measured against a human, so its drop ranks, it does not decide."""
    out = _run_filter(
        tmp_path,
        [
            {
                "email": "a@acme.example",
                "company": "Acme",
                "verdict": "send",
                "judge_verdict": "drop",
                "judge_calibrated": "false",
            }
        ],
        capsys,
    )
    assert "kept 1/1" in out
    assert "NOT calibrated" in out, "the row was kept but the reason was invisible"


def test_a_never_checked_judge_drop_also_does_not_remove_the_row(tmp_path, capsys):
    out = _run_filter(
        tmp_path,
        [
            {
                "email": "a@acme.example",
                "company": "Acme",
                "verdict": "send",
                "judge_verdict": "drop",
                "judge_calibrated": "",
            }
        ],
        capsys,
    )
    assert "kept 1/1" in out


def test_a_calibrated_judge_drop_does_remove_the_row(tmp_path, capsys):
    out = _run_filter(
        tmp_path,
        [
            {
                "email": "a@acme.example",
                "company": "Acme",
                "verdict": "send",
                "judge_verdict": "drop",
                "judge_calibrated": "true",
            }
        ],
        capsys,
    )
    assert "kept 0/1" in out
    assert "judge (calibrated) additionally removed 1 row(s)" in out


def test_a_research_drop_is_refused_whatever_the_judge_says(tmp_path, capsys):
    """The researcher's verdict is the primary gate and a judge `send` cannot override it."""
    out = _run_filter(
        tmp_path,
        [
            {
                "email": "a@acme.example",
                "company": "Acme",
                "verdict": "drop",
                "judge_verdict": "send",
                "judge_calibrated": "true",
            }
        ],
        capsys,
    )
    assert "kept 0/1" in out


def test_a_clean_row_survives_both(tmp_path, capsys):
    """Positive control — the filter must not empty a healthy list."""
    out = _run_filter(
        tmp_path,
        [
            {
                "email": "a@acme.example",
                "company": "Acme",
                "verdict": "send",
                "judge_verdict": "send",
                "judge_calibrated": "true",
            }
        ],
        capsys,
    )
    assert "kept 1/1" in out
