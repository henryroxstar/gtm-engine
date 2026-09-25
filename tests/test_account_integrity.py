"""Tests for the pre-load account-integrity gate.

Every fixture here is invented. The defect *shapes* are real ones observed on a live run
(an academic-domain contact, a research-note artifact leaking into `company`, a dossier
variant that skips leadership re-verification), but the identities are fictional per the
third-party-PII rule (docs/RULES.md R9): real people and real companies live only in
`profiles/` and `content/`.
"""

from __future__ import annotations

import csv
import json
import os
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
    why_now_not_a_signal,
)
from gtm_core.prospect_paths import evals_dir
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
    """The university-mail-domain shape found 2026-08-12 — never a
    legitimate corporate contact, unlike a generic domain divergence."""
    issue, detail = domain_issue(
        _row(
            email="j.vance@riverbend.edu", company="Vertex Systems", company_domain="vertex.example"
        )
    )
    assert issue == DomainIssue.ACADEMIC
    assert "riverbend.edu" in detail


def test_domain_issue_generic_mismatch_is_advisory_not_a_hard_stop():
    """A brand-vs-legal-name or parent/subsidiary domain split (a brand on its holding
    company's domain, a subsidiary on its parent's — both seen in the real run) is
    frequently legitimate — escalating every
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
        "Gears & Vectors",
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
    """The real run's 'Acme Singapore' vs. the watchlist's 'Acme for AI Agents' shape —
    a prospected account is often named by product/region, not the entity the
    watchlist was filed under, so matching must go through aliases AND domains."""
    competitors = {
        "rival": CompetitorHit("direct", "Rival Corp (direct) — Ships an overlapping product."),
    }
    # Matches via domain even though the display name is completely different.
    assert competitor_match("Rival Regional Ops", "rival.example", competitors)
    # Matches via a bare-name token when no domain is on file.
    assert competitor_match("Rival", "", competitors)


def test_load_competitors_indexes_rows_regardless_of_product(tmp_path):
    """A product-scoped row (e.g. `product = "quarry-digital"`) must still be indexed and
    hard-stopped for every product — `load_competitors` never prospects a competitor of
    ANY product. Pins today's behaviour: the field is ignored, not filtered on."""
    root = tmp_path / "profiles"
    (root / PROFILE / "knowledge").mkdir(parents=True)
    (root / PROFILE / "knowledge" / "competitors.toml").write_text(
        'schema = 1\nreviewed = "2026-08-01"\n\n'
        "[[competitor]]\n"
        'name = "Quarry Digital Rival"\n'
        'tier = "direct"\n'
        'product = "quarry-digital"\n'
        'aliases = ["QD Rival"]\n'
        'domains = ["qdrival.example"]\n'
        "watch_urls = []\n"
        'syften_filter = ""\n'
        'first_seen = "2026-08-01"\n'
        'last_reviewed = "2026-08-01"\n'
        'status = "active"\n'
        'note = "Contests Quarry Digital directly."\n',
        encoding="utf-8",
    )
    competitors = load_competitors(PROFILE, root)
    hit = competitor_match("Quarry Digital Rival", "qdrival.example", competitors)
    assert hit is not None
    assert hit.tier == "direct" and hit.direct


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


def test_audit_names_an_ambiguous_account_folder_instead_of_missing_research(tmp_path):
    """AF1: when two existing folders could be the account, the dossier skill cannot place
    a new dossier either (`account_folder` exits 3), so "has no research" would send the
    operator to generate one it cannot write. The finding names the folders instead. It
    stays the `no-dossier` rule, so its lane classification and acks are unchanged."""
    _write_dossier(tmp_path, "vertex-systems-limited", "account-dossier-v-2026-08-01.docx")
    _write_dossier(tmp_path, "vertex-systems-plc", "account-dossier-v-2026-08-01.docx")
    a = audit_rows([_row()], PROFILE, content_root=tmp_path, profiles_root=tmp_path / "profiles")
    assert a.no_dossier == 1
    [finding] = [e for e in a.errors if e.startswith("no-dossier:")]
    assert "folder-ambiguous" in finding
    assert "vertex-systems-limited" in finding and "vertex-systems-plc" in finding
    assert "has no research behind" not in finding


def test_audit_keys_accounts_on_account_id_when_one_row_lacks_a_domain(tmp_path):
    """PH10: one account, two contacts, one row missing ``company_domain``. The org token
    is the domain on one row and the name on the other, so keying on it alone counted the
    account twice and emitted its per-account findings twice. ``account_id`` is the
    account's identity; it wins when present."""
    content_root = tmp_path / "content"
    rows = [
        _row(email="jordan.vance@vertex.example", account_id="acct-0001"),
        _row(
            email="sam.reyes@vertex.example",
            first="Sam",
            last="Reyes",
            company_domain="",
            account_id="acct-0001",
        ),
    ]
    a = audit_rows(rows, PROFILE, content_root=content_root, profiles_root=tmp_path / "profiles")
    assert a.accounts == 1
    assert a.no_dossier == 1
    assert len([e for e in a.errors if "no-dossier" in e]) == 1


def test_audit_without_account_id_still_keys_on_the_org_token(tmp_path):
    """No ``account_id`` on file: the org token is all there is, so a domain-less row is
    still a different key from its domain-carrying sibling (the fallback is unchanged)."""
    content_root = tmp_path / "content"
    rows = [
        _row(email="jordan.vance@vertex.example"),
        _row(email="sam.reyes@vertex.example", first="Sam", last="Reyes", company_domain=""),
    ]
    a = audit_rows(rows, PROFILE, content_root=content_root, profiles_root=tmp_path / "profiles")
    assert a.accounts == 2


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
        [_row(signal_subject="Cascade Financial")],
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
                # No signal at all — not just an empty `signal_clause` column. Since
                # gtm_core.signal_record.check_record now falls back to deriving the
                # clause from `why_now` (PS3, 2026-09-10), leaving the fixture's
                # default `why_now` in place would make this row carry a genuine
                # dated claim with no provenance behind it, adding unrelated
                # signal-* findings this test isn't about — it exists to pin the
                # WARN-tier readability budget on domain-mismatch alone.
                why_now="",
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


def test_an_alias_can_downgrade_an_academic_address_but_never_silence_it(tmp_path):
    """What a tenant-editable data file may and may not do to a hard stop.

    It may DOWNGRADE: academic medical centres (a medical school's .edu beside the clinic
    brand's differently-named .org) genuinely run corporate mail on .edu, and no
    structural test derives those sibling brands. The operator declares the
    pair and the finding becomes a warning a human still reads.

    It may NOT silence: the result is never NONE, so a wrong alias entry costs a warning,
    never an invisible wrong-entity contact. That is the property this test pins.
    """
    profiles_root = tmp_path / "profiles"
    _write_aliases(
        profiles_root,
        'schema = 1\n[[alias]]\ndomains = ["riverbend.edu", "vertex.example"]\nnote = "declared"\n',
    )
    row = _row(email="jordan.vance@riverbend.edu", company_domain="vertex.example")
    issue, _ = domain_issue(row, load_domain_aliases(PROFILE, profiles_root))
    assert issue == DomainIssue.ACADEMIC_MEDICAL
    assert issue != DomainIssue.NONE


def test_an_undeclared_academic_address_is_still_a_hard_stop(tmp_path):
    """The 2026-08-12 defect itself: a stranger's alma mater on a corporate row."""
    profiles_root = tmp_path / "profiles"
    _write_aliases(profiles_root, "schema = 1\n")
    row = _row(email="jordan.vance@riverbend.edu", company_domain="vertex.example")
    issue, _ = domain_issue(row, load_domain_aliases(PROFILE, profiles_root))
    assert issue == DomainIssue.ACADEMIC


def test_an_academic_domain_sharing_the_accounts_own_stem_is_not_a_finding(tmp_path):
    """A `.edu` that is the account's OWN domain on another TLD is not a divergence.

    The live shape this covers is a health system whose staff mail and web presence share
    one registrable stem across two TLDs. Structural, so it needs no operator declaration
    and reports nothing at all — unlike the alias downgrade above, there is genuinely no
    divergence here for a human to read.
    """
    profiles_root = tmp_path / "profiles"
    _write_aliases(profiles_root, "schema = 1\n")
    row = _row(
        email="jo.chen@riverbend.edu", company="Riverbend", company_domain="riverbend.example"
    )
    issue, _ = domain_issue(row, load_domain_aliases(PROFILE, profiles_root))
    assert issue == DomainIssue.NONE


def test_a_company_domain_carrying_a_url_path_still_matches_an_alias(tmp_path):
    """`company_domain` is sometimes 'acme.example/us' — the host is what an alias declares."""
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
    """Resolving a folder lists every account folder and reads the ledger; doing it twice
    per account is pure cost."""
    _write_dossier(tmp_path, "northwind", "account-dossier-northwind-2026-08-01.md")
    calls = []
    real = ai.dossier_folder

    def _counting(*args, **kwargs):
        calls.append(args[:2])
        return real(*args, **kwargs)

    monkeypatch.setattr(ai, "dossier_folder", _counting)
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


def _write_lane_states(content_root: Path, profile: str, recs: list[dict]) -> None:
    state_dir = evals_dir(profile, content_root)
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "lanes-state.jsonl").open("w", encoding="utf-8") as fh:
        for r in recs:
            rec = {
                "email": "",
                "lane": "signal",
                "trigger": "t1",
                "judge_verdict": "",
                "body_hash": "",
                "stamp": "2026-09-01",
            }
            rec.update(r)
            fh.write(json.dumps(rec) + "\n")


def _run_filter(tmp_path, rows, capsys):
    content_root = tmp_path / "content"
    old_root = os.environ.get("GTM_CONTENT_ROOT")
    os.environ["GTM_CONTENT_ROOT"] = str(content_root)
    try:
        recs = [{"email": r.get("email", ""), "lane": "signal"} for r in rows]
        _write_lane_states(content_root, "acme", recs)
        p = _verdict_csv(tmp_path, rows)
        ai.main(
            [
                "--csv",
                str(p),
                "--profile",
                "acme",
                "--require-verdict",
                "send",
                "--warn-only",
                "--lane",
                "signal",
            ]
        )
        return capsys.readouterr().out
    finally:
        if old_root is None:
            os.environ.pop("GTM_CONTENT_ROOT", None)
        else:
            os.environ["GTM_CONTENT_ROOT"] = old_root


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


# --- why_now that records an absence -------------------------------------------


@pytest.mark.parametrize(
    "why_now",
    [
        "No qualifying dated public hit found this pass.",
        "no qualifying dated public hit found for 2026 — but the rebrand is legitimate",
        "Bombora topic-intent: agentic ai (77) — feed signal; dated public why-now not yet confirmed",
        "RocketReach shows in-market on AI Agent Security; no dated public why-now event surfaced",
        "Firmographic + cohort fit only; no intent signal and no confirmed public why-now",
        "No confirmed public signal this pass.",
    ],
)
def test_why_now_not_a_signal_catches_an_empty_sweep(why_now):
    assert "found NOTHING" in why_now_not_a_signal(why_now)


@pytest.mark.parametrize(
    "why_now",
    [
        "DISQUALIFIED — absorbed into a parent system, not a separately addressable buyer.",
        "Startup-segment verification: score 1/10, does not clear publish floor (6).",
        "Not a plausible independent buyer — facility-level entity inside a larger system.",
    ],
)
def test_why_now_not_a_signal_catches_a_disqualification(why_now):
    assert "DISQUALIFICATION" in why_now_not_a_signal(why_now)


@pytest.mark.parametrize(
    "why_now",
    [
        "Vertex Systems opened an AI governance program in 2026",
        # Contains 'no'/'not' but states a real, dated signal — the words alone must
        # not be the trigger, or every clause with a negation in it blocks a send.
        "Vertex Systems said the rollout will not extend to third-party agents until Q4",
        "Vertex announced no-code agent tooling for enterprise customers on 2026-05-11",
        "Publicly deploying agents through a GenAI Centre of Excellence; no timeline given",
        "",
    ],
)
def test_why_now_not_a_signal_leaves_a_real_clause_alone(why_now):
    assert why_now_not_a_signal(why_now) == ""


def test_a_negative_why_now_is_an_error_not_a_warning(tmp_path):
    """It blocks. A row that admits it has no signal would open its email with that
    sentence, so this is never something the operator reads past."""
    content_root = tmp_path / "content"
    _write_dossier(content_root, "vertex-systems", "dossier-vertex-systems-2026-08-12.md")
    a = audit_rows(
        [_row(why_now="No qualifying dated public hit found this pass.")],
        PROFILE,
        content_root=content_root,
        profiles_root=tmp_path / "profiles",
    )
    assert a.why_now_not_signal == 1
    assert a.failed
    assert any(e.startswith("why-now-not-a-signal") for e in a.errors)
    assert not any(w.startswith("why-now-not-a-signal") for w in a.warnings)


def test_provenance_does_not_rescue_a_stale_negative_why_now(tmp_path):
    """The defect this rule exists for, in its exact observed shape.

    A researcher found a signal on a later pass, wrote `signal_source_url` and
    `signal_evidence`, set `verdict: send` — and never rewrote `why_now`, which still
    holds the earlier "nothing found" text. Every other check passes: the record is
    complete, the dossier is on disk, the verdict says send. Only reading the clause
    itself catches it. Measured 2026-08-30: 2 such rows would have shipped.
    """
    content_root = tmp_path / "content"
    _write_dossier(content_root, "vertex-systems", "dossier-vertex-systems-2026-08-12.md")
    a = audit_rows(
        [
            _row(
                why_now="No qualifying dated public hit found this pass.",
                signal_source_url="https://vertexsystems.example/news/new-chief-digital-officer",
                signal_evidence="Vertex Systems appointed a Chief Digital Officer on 2026-08-01.",
                verdict="send",
            )
        ],
        PROFILE,
        content_root=content_root,
        profiles_root=tmp_path / "profiles",
    )
    assert a.no_dossier == 0  # the dossier is there
    assert a.why_now_not_signal == 1  # and the clause is still wrong
    assert a.failed


# --------------------------------------------------------- lanes (2026-09-03)


def _lane_rows():
    return [
        {"email": "a@x.example", "verdict": "send"},
        {"email": "b@x.example", "verdict": "re-angle"},
        {"email": "c@x.example", "verdict": ""},
        {"email": "d@x.example", "verdict": "drop"},
    ]


def test_signal_lane_default_is_unchanged():
    kept_default, s1 = ai.filter_by_verdict(_lane_rows(), "send")
    kept_signal, s2 = ai.filter_by_verdict(_lane_rows(), "send", lane="signal")
    assert [r["email"] for r in kept_default] == ["a@x.example"]
    assert [r["email"] for r in kept_signal] == ["a@x.example"]
    assert s1.kept == s2.kept == 1


def test_generic_lane_admits_re_angle_and_empty_verdict():
    """The 461 stranded rows: re-angle and never-researched rows may take the seat email."""
    kept, stats = ai.filter_by_verdict(_lane_rows(), "send", lane="generic")
    assert [r["email"] for r in kept] == ["a@x.example", "b@x.example", "c@x.example"]
    assert stats.kept == 3, "drop still enrols nowhere"


def test_generic_lane_never_removes_on_a_calibrated_judge_drop():
    """The judge read the personalised body; its drop is WHY the row is generic."""
    rows = [
        {
            "email": "a@x.example",
            "verdict": "send",
            "judge_verdict": "drop",
            "judge_calibrated": "true",
        }
    ]
    kept_signal, s_sig = ai.filter_by_verdict(rows, "send", lane="signal")
    kept_generic, s_gen = ai.filter_by_verdict(rows, "send", lane="generic")
    assert kept_signal == [] and s_sig.judge_dropped == 1
    assert len(kept_generic) == 1 and s_gen.judge_advisory == 1 and s_gen.judge_dropped == 0


def test_unknown_lane_is_refused():
    with pytest.raises(ValueError):
        ai.filter_by_verdict([], "send", lane="fast")


def test_csv_lane_column_must_match_the_lane_flag(tmp_path, capsys, monkeypatch):
    """Positive control first: a generic-routed CSV enrolled with --lane signal is refused."""
    content_root = tmp_path / "content"
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content_root))
    _write_lane_states(content_root, "acme", [{"email": "a@acme.example", "lane": "generic"}])
    p = tmp_path / "list.csv"
    cols = ["email", "company", "company_domain", "verdict", "lane"]
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerow(
            {
                "email": "a@acme.example",
                "company": "Acme",
                "company_domain": "acme.example",
                "verdict": "re-angle",
                "lane": "generic",
            }
        )
    base = ["--csv", str(p), "--profile", "acme", "--require-verdict", "send", "--warn-only"]
    assert ai.main([*base, "--lane", "signal"]) == 2
    assert "REFUSED" in capsys.readouterr().err
    assert ai.main([*base, "--lane", "generic"]) == 0
    assert "kept 1/1" in capsys.readouterr().out


def test_generic_lane_downgrades_research_absence_but_keeps_every_other_error(tmp_path):
    root = _write_competitors(tmp_path)
    rows = [
        _row(email="a@vertex.example", verdict="", category_relation=""),
        _row(
            email="b@rival.example",
            company="Rival Corp",
            company_domain="rival.example",
            verdict="",
        ),
        _row(
            email="c@riverbend.edu",
            company="Unico",
            company_domain="unico.example",
            verdict="",
        ),
    ]
    signal = audit_rows(rows, PROFILE, content_root=tmp_path, profiles_root=root)
    generic = audit_rows(rows, PROFILE, content_root=tmp_path, profiles_root=root, lane="generic")

    def rules(lines):
        return {line.split(":", 1)[0] for line in lines}

    assert {"no-dossier", "verdict-missing", "relation-unresolved", "competitor-direct"} <= rules(
        signal.errors
    )
    assert "domain-academic" in rules(signal.errors)
    assert not ({"no-dossier", "verdict-missing", "relation-unresolved"} & rules(generic.errors))
    assert {"competitor-direct", "domain-academic"} <= rules(generic.errors), (
        "only the three research-absence classes may be advisory in the generic lane"
    )
    assert any(line.startswith("no-dossier:") and "GENERIC" in line for line in generic.warnings)
    # One aggregate line per class, not one per row — the WARN budget must survive a
    assert sum(1 for line in generic.warnings if line.startswith("verdict-missing:")) == 1


def test_generic_lane_demotes_signal_and_agent_kind_errors(tmp_path):
    """PS-R I4: In the generic lane, signal-* and agent-kind-* errors must be demoted
    to warnings because generic templates do not reference research copy."""
    rows = [
        _row(
            email="a@vertex.example",
            why_now="enterprise automation platform (intent score 81)",
            signal_source_url="",
            signal_agent_kind="",
            verdict="send",
            category_relation="prospect",
        )
    ]
    signal_audit = audit_rows(rows, PROFILE, content_root=tmp_path, lane="signal")
    generic_audit = audit_rows(rows, PROFILE, content_root=tmp_path, lane="generic")

    signal_rules = {e.split(":", 1)[0] for e in signal_audit.errors}
    assert "signal-source-missing" in signal_rules or "signal-clause-underivable" in signal_rules
    assert "agent-kind-unresolved" in signal_rules

    generic_rules = {e.split(":", 1)[0] for e in generic_audit.errors}
    assert "signal-source-missing" not in generic_rules
    assert "signal-clause-underivable" not in generic_rules
    assert "agent-kind-unresolved" not in generic_rules
    assert any("signal-" in line for line in generic_audit.warnings)
    assert any("agent-kind-" in line for line in generic_audit.warnings)


def test_every_enrollable_lane_can_be_named_at_the_gate(tmp_path, capsys, monkeypatch):
    """§R18 — a lane-consistency check the operator cannot reach is not a check.

    Until 2026-09-09 `--lane` carried a hand-written choices list that omitted
    `personalised`, while both LANE_VERDICTS and `gtm_core.lanes` defined it. A
    personalised-routed CSV was therefore refused under `--lane signal` (the lane-column
    guard, working correctly) and the operator's only remaining route was to OMIT the flag
    — which skips that guard for every list, not just this one. No wrong row was admitted,
    because `personalised` and the empty lane admit the same verdict set today; the cost
    was that the guard went dark on exactly the lane whose rows make per-recipient claims.

    `hold` and `excluded` stay unnameable on purpose: they never enrol, so they are absent
    from LANE_VERDICTS, and `filter_by_verdict` refuses them.
    """
    content_root = tmp_path / "content"
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content_root))
    _write_lane_states(content_root, "acme", [{"email": "b@acme.example", "lane": "personalised"}])
    p = tmp_path / "list.csv"
    cols = ["email", "company", "company_domain", "verdict", "lane"]
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerow(
            {
                "email": "b@acme.example",
                "company": "Acme",
                "company_domain": "acme.example",
                "verdict": "send",
                "lane": "personalised",
            }
        )
    base = ["--csv", str(p), "--profile", "acme", "--require-verdict", "send", "--warn-only"]

    # The regression: this used to exit 2 from argparse, because `personalised` was not a
    # choice. It must now reach the gate and pass the lane-column guard.
    assert ai.main([*base, "--lane", "personalised"]) == 0
    assert "kept 1/1" in capsys.readouterr().out

    # Negative control — the guard still convicts a genuine mis-route.
    assert ai.main([*base, "--lane", "generic"]) == 2
    assert "REFUSED" in capsys.readouterr().err

    # A non-enrolling lane is still unnameable, so the choices list cannot become a
    # rubber stamp for every string the router writes.
    with pytest.raises(SystemExit):
        ai.main([*base, "--lane", "excluded"])


# --------------------------------------------------------- PS1: hold/excluded (2026-09-10)
#
# `hold` and `excluded` never enrol anywhere (LANE_VERDICTS omits them on purpose — see
# `test_every_enrollable_lane_can_be_named_at_the_gate` above), but until this gains an
# `else` branch, a CSV carrying either lane reached ready-to-load via the exact
# *documented* command — `--require-verdict send`, no `--lane` — because the existing
# lane-column check only ever runs when `--lane` IS passed. Live 2026-09-10 tenant
# pool: 52 `excluded/already-enrolled` + 14 `hold` rows at verdict=send, reachable this
# way.


def _write_lane_csv(path: Path, cols: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _write_lane_state(content_root: Path, profile: str, **fields) -> None:
    """Write one `lanes-state.jsonl` row for ``profile`` under ``content_root``,
    creating ``evals/`` as needed — the record `lanes route` itself appends, and
    `_refuse_lane_state_mismatch` reads. Caller still owns
    ``monkeypatch.setenv("GTM_CONTENT_ROOT", ...)``; this only writes the file."""
    rec = {
        "email": "",
        "lane": "",
        "trigger": "t1",
        "judge_verdict": "",
        "body_hash": "",
        "stamp": "2026-09-01",
    }
    rec.update(fields)
    state_dir = evals_dir(profile, content_root)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "lanes-state.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")


def test_hold_and_excluded_rows_are_refused_by_the_documented_enrollment_command(
    tmp_path, capsys, monkeypatch
):
    """The exact live shape: `excluded/already-enrolled` and `hold` rows both present,
    enrolled with `--require-verdict send` and no `--lane` — the documented command."""
    content_root = tmp_path / "content"
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content_root))
    _write_lane_states(
        content_root,
        "acme",
        [
            {
                "email": "a@acme.example",
                "lane": "excluded",
                "reason": "already-enrolled",
            },
            {
                "email": "b@acme.example",
                "lane": "excluded",
                "reason": "already-enrolled",
            },
            {
                "email": "c@acme.example",
                "lane": "hold",
                "reason": "",
            },
        ],
    )
    p = tmp_path / "list.csv"
    cols = ["email", "company", "company_domain", "verdict", "lane", "lane_reason"]
    _write_lane_csv(
        p,
        cols,
        [
            {
                "email": "a@acme.example",
                "company": "Acme A",
                "company_domain": "acmea.example",
                "verdict": "send",
                "lane": "excluded",
                "lane_reason": "already-enrolled",
            },
            {
                "email": "b@acme.example",
                "company": "Acme B",
                "company_domain": "acmeb.example",
                "verdict": "send",
                "lane": "excluded",
                "lane_reason": "already-enrolled",
            },
            {
                "email": "c@acme.example",
                "company": "Acme C",
                "company_domain": "acmec.example",
                "verdict": "send",
                "lane": "hold",
                "lane_reason": "",
            },
        ],
    )
    rc = ai.main(
        ["--csv", str(p), "--profile", "acme", "--require-verdict", "send", "--lane", "signal"]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "REFUSED" in err
    # Both counts named: 2 excluded/already-enrolled + 1 hold, 3 total at verdict=send.
    assert "excluded" in err and "already-enrolled" in err and "hold" in err
    assert "3 row(s)" in err
    assert "3 of them are at verdict" in err


def test_two_enrollable_lanes_with_no_lane_flag_is_refused(tmp_path, capsys, monkeypatch):
    """No hold/excluded row here — the second half of PS1: a CSV that mixes two
    lanes THAT DO enrol (`signal` and `generic`) with no `--lane` to say which."""
    content_root = tmp_path / "content"
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content_root))
    _write_lane_states(
        content_root,
        "acme",
        [
            {"email": "a@acme.example", "lane": "signal"},
            {"email": "b@acme.example", "lane": "generic"},
        ],
    )
    p = tmp_path / "list.csv"
    cols = ["email", "company", "company_domain", "verdict", "lane"]
    _write_lane_csv(
        p,
        cols,
        [
            {
                "email": "a@acme.example",
                "company": "Acme A",
                "company_domain": "acmea.example",
                "verdict": "send",
                "lane": "signal",
            },
            {
                "email": "b@acme.example",
                "company": "Acme B",
                "company_domain": "acmeb.example",
                "verdict": "send",
                "lane": "generic",
            },
        ],
    )
    rc = ai.main(
        ["--csv", str(p), "--profile", "acme", "--require-verdict", "send", "--lane", "signal"]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "REFUSED" in err
    assert "generic" in err and "signal" in err


def test_refuse_ambiguous_lane_ignores_blank_lane_values():
    """Direct reproduction of the reported false positive: `_refuse_ambiguous_lane`
    (unlike its sibling `_refuse_lane_state_mismatch`'s `foreign` computation, which
    already excluded `""`) did not exclude a blank `lane` value from the enrollable-
    lanes set, so a blank-lane row plus one real enrollable lane read as TWO enrollable
    lanes (`['', 'signal']`) and was wrongly refused. A blank lane names no lane at
    all — it must not count as a second one."""
    rows = [{"lane": "", "verdict": "send"}, {"lane": "signal", "verdict": "send"}]
    assert ai._refuse_ambiguous_lane(rows) is None


def test_a_blank_lane_row_does_not_count_as_a_second_enrollable_lane(tmp_path, capsys, monkeypatch):
    """End-to-end version of the regression above, through the documented enrollment
    command: a CSV mixing a genuinely blank `lane` (plausible on a hand-edited or
    merged list, per PS2's docstring) with exactly one real lane must not be refused
    for carrying "more than one enrollable lane"."""
    content_root = tmp_path / "content"
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content_root))
    _write_lane_states(
        content_root,
        "acme",
        [
            {"email": "a@acme.example", "lane": "signal"},
            {"email": "b@acme.example", "lane": "signal"},
        ],
    )
    p = tmp_path / "list.csv"
    cols = ["email", "company", "company_domain", "verdict", "lane"]
    _write_lane_csv(
        p,
        cols,
        [
            {
                "email": "a@acme.example",
                "company": "Acme A",
                "company_domain": "acmea.example",
                "verdict": "send",
                "lane": "",
            },
            {
                "email": "b@acme.example",
                "company": "Acme B",
                "company_domain": "acmeb.example",
                "verdict": "send",
                "lane": "signal",
            },
        ],
    )
    rc = ai.main(
        [
            "--csv",
            str(p),
            "--profile",
            "acme",
            "--require-verdict",
            "send",
            "--warn-only",
            "--lane",
            "signal",
        ]
    )
    err = capsys.readouterr().err
    assert "REFUSED" not in err
    assert rc == 0


def test_a_plain_audit_is_unaffected_by_the_hold_excluded_check(tmp_path, capsys):
    """Same shape as the positive control above, but with no `--require-verdict` at all.

    **Changed 2026-09-23 with `--lane` becoming required, and the change is the point.**
    This used to assert that a plain audit skipped the lane-column check — which was true
    only because the check runs when a lane is named, and a plain audit could decline to
    name one. There is no longer an unlaned invocation to decline with, so a CSV whose rows
    are routed to `hold` / `excluded` is now refused under every enrollable lane rather than
    audited as though it were enrollable.

    That is the honest answer, not a regression: `hold` and `excluded` are not lanes you can
    enrol into, so the alternative is printing PASS over a list of held rows — the same
    false reassurance as a `ready_to_send` label that claims completion before the check has
    run. It exits 2 with the reason named, never a silent skip.
    """
    p = tmp_path / "list.csv"
    cols = ["email", "company", "company_domain", "verdict", "lane", "lane_reason"]
    _write_lane_csv(
        p,
        cols,
        [
            {
                "email": "a@acme.example",
                "company": "Acme A",
                "company_domain": "acmea.example",
                "verdict": "send",
                "lane": "excluded",
                "lane_reason": "already-enrolled",
            },
            {
                "email": "c@acme.example",
                "company": "Acme C",
                "company_domain": "acmec.example",
                "verdict": "send",
                "lane": "hold",
                "lane_reason": "",
            },
        ],
    )
    rc = ai.main(["--csv", str(p), "--profile", "acme", "--warn-only", "--lane", "signal"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err
    # The reason must name both foreign values, so the operator can see it is the CSV's
    # routing that disagrees and not their flag that is misspelled.
    assert "excluded" in err and "hold" in err


# --------------------------------------------------------- PS2: lane-state gate half (2026-09-10)
#
# `lanes route`'s own `evals/lanes-state.jsonl` records the LAST lane it decided for
# each email. A CSV whose own `lane` column disagrees — re-routed since the CSV was
# cut, or hand-edited — must not enrol on the strength of the stale column. The route
# half of this (re-deriving/re-running the router) is Track B's job; this is the gate
# half only: read the state file and refuse a disagreement.


def test_a_csv_lane_disagreeing_with_lanes_state_is_refused(tmp_path, monkeypatch, capsys):
    content_root = tmp_path / "content"
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content_root))
    _write_lane_state(content_root, "acme", email="a@acme.example", lane="generic")
    p = tmp_path / "list.csv"
    cols = ["email", "company", "company_domain", "verdict", "lane"]
    _write_lane_csv(
        p,
        cols,
        [
            {
                "email": "a@acme.example",
                "company": "Acme A",
                "company_domain": "acmea.example",
                "verdict": "send",
                "lane": "signal",  # disagrees with the state file's "generic"
            }
        ],
    )
    rc = ai.main(["--csv", str(p), "--profile", "acme", "--warn-only", "--lane", "signal"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "REFUSED" in err
    assert "lanes-state.jsonl" in err


def test_a_csv_lane_agreeing_with_lanes_state_is_not_refused(tmp_path, monkeypatch, capsys):
    """Positive control — the same lane on both sides is not a disagreement."""
    content_root = tmp_path / "content"
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content_root))
    _write_lane_state(content_root, "acme", email="a@acme.example", lane="signal")
    p = tmp_path / "list.csv"
    cols = ["email", "company", "company_domain", "verdict", "lane"]
    _write_lane_csv(
        p,
        cols,
        [
            {
                "email": "a@acme.example",
                "company": "Acme A",
                "company_domain": "acmea.example",
                "verdict": "send",
                "lane": "signal",
            }
        ],
    )
    rc = ai.main(["--csv", str(p), "--profile", "acme", "--warn-only", "--lane", "signal"])
    assert rc == 0


def test_a_missing_lanes_state_file_skips_the_check_not_an_error(tmp_path, monkeypatch, capsys):
    """No state file yet — nothing has been routed for this profile — is not the same
    as a disagreement; it must not refuse."""
    content_root = tmp_path / "content"
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content_root))
    p = tmp_path / "list.csv"
    cols = ["email", "company", "company_domain", "verdict", "lane"]
    _write_lane_csv(
        p,
        cols,
        [
            {
                "email": "a@acme.example",
                "company": "Acme A",
                "company_domain": "acmea.example",
                "verdict": "send",
                "lane": "signal",
            }
        ],
    )
    rc = ai.main(["--csv", str(p), "--profile", "acme", "--warn-only", "--lane", "signal"])
    assert rc == 0


# --------------------------------------------------------- PS-R I1 & I2: Enrollment gates (2026-09-11)


def test_i1_missing_lanes_state_refused_under_require_verdict(tmp_path, capsys, monkeypatch):
    content_root = tmp_path / "content"
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content_root))
    p = tmp_path / "list.csv"
    _write_lane_csv(
        p,
        ["email", "company", "verdict"],
        [{"email": "a@acme.example", "company": "Acme", "verdict": "send"}],
    )
    rc = ai.main(
        ["--csv", str(p), "--profile", "acme", "--require-verdict", "send", "--lane", "signal"]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "REFUSED: evals/lanes-state.jsonl is missing" in err


def test_i1_empty_lanes_state_refused_under_require_verdict(tmp_path, capsys, monkeypatch):
    content_root = tmp_path / "content"
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content_root))
    state_dir = evals_dir("acme", content_root)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "lanes-state.jsonl").write_text("\n", encoding="utf-8")
    p = tmp_path / "list.csv"
    _write_lane_csv(
        p,
        ["email", "company", "verdict"],
        [{"email": "a@acme.example", "company": "Acme", "verdict": "send"}],
    )
    rc = ai.main(
        ["--csv", str(p), "--profile", "acme", "--require-verdict", "send", "--lane", "signal"]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "REFUSED: evals/lanes-state.jsonl is empty" in err


def test_i1_unrouted_row_refused_under_require_verdict(tmp_path, capsys, monkeypatch):
    content_root = tmp_path / "content"
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content_root))
    _write_lane_states(content_root, "acme", [{"email": "a@acme.example", "lane": "signal"}])
    p = tmp_path / "list.csv"
    _write_lane_csv(
        p,
        ["email", "company", "verdict"],
        [
            {"email": "a@acme.example", "company": "Acme", "verdict": "send"},
            {"email": "unrouted@acme.example", "company": "Acme", "verdict": "send"},
        ],
    )
    rc = ai.main(
        ["--csv", str(p), "--profile", "acme", "--require-verdict", "send", "--lane", "signal"]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "REFUSED: 1 row(s) missing from evals/lanes-state.jsonl" in err


def test_i1_blank_lane_in_state_refused_under_require_verdict(tmp_path, capsys, monkeypatch):
    content_root = tmp_path / "content"
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content_root))
    _write_lane_states(content_root, "acme", [{"email": "a@acme.example", "lane": ""}])
    p = tmp_path / "list.csv"
    _write_lane_csv(
        p,
        ["email", "company", "verdict"],
        [{"email": "a@acme.example", "company": "Acme", "verdict": "send"}],
    )
    rc = ai.main(
        ["--csv", str(p), "--profile", "acme", "--require-verdict", "send", "--lane", "signal"]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "have blank lane in evals/lanes-state.jsonl" in err


def test_i1_hold_or_excluded_in_state_refused_even_without_lane_column(
    tmp_path, capsys, monkeypatch
):
    content_root = tmp_path / "content"
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content_root))
    _write_lane_states(
        content_root,
        "acme",
        [{"email": "a@acme.example", "lane": "hold", "reason": "negative-reply"}],
    )
    # CSV has NO lane column at all
    p = tmp_path / "list.csv"
    _write_lane_csv(
        p,
        ["email", "company", "verdict"],
        [{"email": "a@acme.example", "company": "Acme", "verdict": "send"}],
    )
    rc = ai.main(
        ["--csv", str(p), "--profile", "acme", "--require-verdict", "send", "--lane", "signal"]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "carry lane 'hold' or 'excluded'" in err


def test_i2_retired_account_refused_under_require_verdict(tmp_path, capsys, monkeypatch):
    content_root = tmp_path / "content"
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content_root))
    _write_lane_states(content_root, "acme", [{"email": "a@acme.example", "lane": "signal"}])
    prospects_dir = content_root / "acme" / "prospects"
    prospects_dir.mkdir(parents=True, exist_ok=True)
    (prospects_dir / "latest.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-09-11T00:00:00Z",
                "profile": "acme",
                "items": [
                    {
                        "company": "Acme",
                        "domain": "acme.example",
                        "status": "disqualified",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    p = tmp_path / "list.csv"
    _write_lane_csv(
        p,
        ["email", "company", "company_domain", "verdict"],
        [
            {
                "email": "a@acme.example",
                "company": "Acme",
                "company_domain": "acme.example",
                "verdict": "send",
            }
        ],
    )
    rc = ai.main(
        ["--csv", str(p), "--profile", "acme", "--require-verdict", "send", "--lane", "signal"]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert (
        "REFUSED: 1 row(s) belong to accounts with retired or engaged status in latest.json (disqualified: 1)"
        in err
    )


def test_i2_engaged_account_refused_under_require_verdict(tmp_path, capsys, monkeypatch):
    content_root = tmp_path / "content"
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content_root))
    _write_lane_states(content_root, "acme", [{"email": "a@acme.example", "lane": "signal"}])
    prospects_dir = content_root / "acme" / "prospects"
    prospects_dir.mkdir(parents=True, exist_ok=True)
    (prospects_dir / "latest.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-09-11T00:00:00Z",
                "profile": "acme",
                "items": [
                    {
                        "contact_email": "a@acme.example",
                        "company": "Acme",
                        "status": "replied",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    p = tmp_path / "list.csv"
    _write_lane_csv(
        p,
        ["email", "company", "verdict"],
        [{"email": "a@acme.example", "company": "Acme", "verdict": "send"}],
    )
    rc = ai.main(
        ["--csv", str(p), "--profile", "acme", "--require-verdict", "send", "--lane", "signal"]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert (
        "REFUSED: 1 row(s) belong to accounts with retired or engaged status in latest.json (replied: 1)"
        in err
    )


def test_i2_unblocked_account_passes_under_require_verdict(tmp_path, capsys, monkeypatch):
    content_root = tmp_path / "content"
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content_root))
    _write_lane_states(content_root, "acme", [{"email": "a@acme.example", "lane": "signal"}])
    prospects_dir = content_root / "acme" / "prospects"
    prospects_dir.mkdir(parents=True, exist_ok=True)
    (prospects_dir / "latest.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-09-11T00:00:00Z",
                "profile": "acme",
                "items": [
                    {
                        "company": "Acme",
                        "domain": "acme.example",
                        "status": "new",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    p = tmp_path / "list.csv"
    _write_lane_csv(
        p,
        ["email", "company", "company_domain", "verdict"],
        [
            {
                "email": "a@acme.example",
                "company": "Acme",
                "company_domain": "acme.example",
                "verdict": "send",
            }
        ],
    )
    rc = ai.main(
        [
            "--csv",
            str(p),
            "--profile",
            "acme",
            "--require-verdict",
            "send",
            "--warn-only",
            "--lane",
            "signal",
        ]
    )
    err = capsys.readouterr().err
    assert "REFUSED" not in err
    assert rc == 0


def test_generic_lane_advisory_classes_do_not_count_toward_the_budget(tmp_path):
    """The research-absence classes were acked by rote on every generic run (ten of them,
    every time). Since 2026-09-24 they print and do not spend the budget — the same
    arithmetic as an acked class, without the operator having to say so."""
    rows = [_row(email=f"p{i}@vertex.example", verdict="", category_relation="") for i in range(3)]
    generic = audit_rows(rows, PROFILE, content_root=tmp_path, lane="generic")
    assert {"no-dossier", "verdict-missing", "relation-unresolved"} <= set(generic.advisory)
    assert set(generic.advisory) <= ai.GENERIC_LANE_ADVISORY
    unacked = {c.rule for c in generic.warn_verdict.unacked_classes}
    assert not (set(generic.advisory) & unacked)
    # Negative control: the same demoted lines, not marked advisory, DO count.
    generic.advisory = ()
    assert "no-dossier" in {c.rule for c in generic.warn_verdict.unacked_classes}
