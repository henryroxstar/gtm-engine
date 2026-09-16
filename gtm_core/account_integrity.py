"""Pre-load account-integrity gate — the checks that sit between "does this row
render" (:mod:`tests.linter.merge_render_linter`) and "would sending it embarrass us."

None of the existing gates ask whether the ACCOUNT behind a row is safe to write to.
:func:`gtm_core.merge_hygiene.check_row` already computes a mismatched email domain,
but only as an advisory ``warn`` that nothing downstream re-surfaces before a batch
loads — on 2026-08-12 that let a run ship live with four contacts whose mail domain was
a university's or a non-profit's rather than their employer's on file,
caught only by a human spot-check after the fact. The same run staged 439 of 496
contacts with zero account research behind their opener, reached at least one account
whose CEO had died nine days earlier, and pitched roughly a dozen direct competitors —
none of it flagged anywhere in the pipeline.

Seven check families, all account- or row-level, none of which exists elsewhere:

* ``no-dossier`` (ERROR) — the account has no research behind its Why Now clause.
  Reuses :func:`gtm_core.prospects_consolidate.account_has_dossier` rather than
  re-deriving dossier-existence logic a second time.
* ``domain-mismatch`` (ERROR) — escalates ``check_row``'s advisory
  ``email-domain-mismatch`` finding into a load-time block. This module does not
  re-derive the domain logic; it re-grades the SAME finding at a different moment in
  the pipeline (load time, not consolidation time) so it is never silently generated
  and never read.
* ``stale-artifact-string`` (ERROR) — a disambiguation suffix ("Northwind Parent") that
  leaked from research notes into the live ``company`` merge field.
* ``competitor-direct`` (ERROR) / ``competitor-flag`` (WARN) — the account ships a
  competing/adjacent product, per the profile's own maintained
  ``knowledge/competitors.toml``. Graded **by the tier the profile already recorded**:
  a ``direct`` competitor is a hard stop, everything else stays a WARN a human frames.
  This was one flat WARN until 2026-08-19, when a measurement showed the gate had named
  four direct competitors outright inside a 388-warning block that was acknowledged
  wholesale. The finding was never missed; it was never *read*.
* ``leadership-freshness`` (WARN) — the account's only dossier is the
  ``prospecting-brief`` variant, which by design skips the fresh leadership re-check
  the full dossier runs (see ``account-dossier/SKILL.md`` §"Prospecting brief
  variant"). Not a leadership-change detector; a flag that the check was never
  attempted, so a human knows the blind spot exists.
* ``verdict-*`` / ``signal-*`` / ``relation-*`` (mixed) — the row's **research record**
  (:mod:`gtm_core.signal_record`): source URL, observed date, verbatim evidence, the
  fact's real subject, the agent-homonym classification, and the send/re-angle/drop
  verdict. Owned entirely by that module; this gate owns the *moment* it runs, the same
  arrangement it already has with ``check_row``. A list predating the record columns
  produces one file-level finding, never one per row.

Fail-closed like the rest of the gate family: an account with no dossier, an
unverifiable domain claim, or no competitor list to check against is a finding or a
skipped check, never assumed-fine.

**Output is budgeted** (:mod:`gtm_core.finding_budget`). Past ~15 unacknowledged
warnings the WARN tier stops enumerating, reports each class as a rate with exemplars,
and blocks. "Needs one explicit operator acknowledgment" is a real instruction at ten
findings and a fiction at 388; a gate whose output cannot be read has the same effect
as a gate that never ran, which is what happened here.

Deliberately NOT here: copy/voice quality (``outreach_pack_linter``), merge-tag
mechanics (``merge_render_linter``), list-level targeting (``gtm_core.list_fit``),
or legal/compliance (``gtm_core.email_compliance``). Those have owners. This module
owns *is the account itself safe to write to*.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .enrollment_gate import (
    _refuse_ambiguous_lane,
    _refuse_lane_state_mismatch,
    check_account_status,
    check_enrollment_lanes,
)
from .finding_budget import WARN_BUDGET, budget_verdict, render_budget
from .lane_verdicts import LANE_VERDICTS as _LANE_VERDICTS
from .merge_hygiene import check_row
from .paths import _safe_segment, resolve_content_root, resolve_profiles_root
from .prospect_paths import suppression_ledger
from .prospects_consolidate import (
    DOSSIER_GLOB_BRIEF,
    DOSSIER_GLOB_FULL,
    DOSSIER_GLOB_ONEPAGER,
    account_has_dossier,
    org_token,
)
from .signal_record import audit_records
from .suppression import load_index as load_suppression_index

__all__ = [
    "DossierDepth",
    "DomainIssue",
    "CompetitorHit",
    "DIRECT_TIER",
    "AccountAudit",
    "VerdictFilterStats",
    "_refuse_ambiguous_lane",
    "dossier_depth",
    "domain_issue",
    "stale_artifact_string",
    "load_competitors",
    "competitor_match",
    "filter_by_verdict",
    "audit_rows",
    "render",
    "main",
]

# --- dossier depth ---------------------------------------------------------


class DossierDepth:
    NONE = "none"
    #: The prospecting-brief variant — by design skips fresh leadership re-verification.
    BRIEF = "brief"
    ONEPAGER = "onepager"
    #: The full ≤4-page dossier — the only variant that re-checks leadership (Step 4).
    FULL = "full"


def dossier_depth(
    profile: str, company: str, company_domain: str = "", content_root: Path | None = None
) -> str:
    """Which dossier variant (if any) exists for this account, checked cheapest-first
    so a ``full`` dossier is never misclassified as merely a brief. Reuses
    :func:`gtm_core.prospects_consolidate.account_has_dossier` for the canonical-slug +
    fuzzy-folder match; this only classifies what it found.
    """
    has, folder_name = account_has_dossier(profile, company, company_domain, content_root)
    return classify_dossier_folder(profile, folder_name, content_root) if has else DossierDepth.NONE


def classify_dossier_folder(
    profile: str, folder_name: str, content_root: Path | None = None
) -> str:
    """Classify an already-located account folder, without re-running the folder scan.

    Split out so a caller that has already resolved the folder (the row audit does,
    for every account) classifies it with one filesystem walk instead of two — the
    fuzzy match re-scans every account folder under the profile.
    """
    if not folder_name:
        return DossierDepth.NONE
    root = content_root or resolve_content_root()
    # ``profile`` reaches here from --profile; guard it as a bare segment before it is
    # joined, exactly as the canonical accounts-dir helper does. Directory traversal
    # here is the highest-risk tenant error.
    folder = root / _safe_segment(profile, "profile") / "accounts" / folder_name
    if any(any(folder.glob(p)) for p in DOSSIER_GLOB_FULL):
        return DossierDepth.FULL
    if any(any(folder.glob(p)) for p in DOSSIER_GLOB_ONEPAGER):
        return DossierDepth.ONEPAGER
    if any(any(folder.glob(p)) for p in DOSSIER_GLOB_BRIEF):
        return DossierDepth.BRIEF
    return DossierDepth.NONE  # matched folder but no known glob hit — fail-closed, not assumed-full


# --- domain integrity --------------------------------------------------------


class DomainIssue:
    NONE = "none"
    #: Free webmail — common for a founder/startup contact, not a hard stop on its own.
    PERSONAL = "personal"
    #: A domain that diverges from the company's own on file. Frequently benign — a
    #: parent/subsidiary or brand-vs-legal-name split (a retail brand mailing from its
    #: holding company's domain, a subsidiary from its parent's) — so this alone is a WARN, not
    #: a block. Escalating every one of these to ERROR was tried first and produced
    #: mostly noise on real accounts; see ACADEMIC below for the one shape worth a hard
    #: stop.
    MISMATCH = "mismatch"
    #: An academic-institution domain (.edu / .ac.xx) on what is supposed to be a
    #: corporate contact — the university-mail-domain shape found
    #: 2026-08-12. A real enterprise buyer does not correspond from their alma mater's
    #: mail server, so this is a hard stop — but see ACADEMIC_MEDICAL for the one class
    #: where the reading IS legitimate.
    ACADEMIC = "academic"
    #: An academic domain on an ACADEMIC MEDICAL CENTRE. This rule used to assert there
    #: was "no legitimate reading" of a .edu contact, and that was simply wrong about a
    #: whole industry: the large academic medical centres genuinely run their corporate
    #: mail on .edu, and for several of them the .edu IS the account's own registered
    #: domain on another TLD. Nine live rows were being
    #: hard-blocked on 2026-08-29 for using their real work address. Downgraded to a WARN
    #: rather than dropped: an academic address on a health system is worth one look
    #: (a clinician is not always the buyer), just never a block.
    ACADEMIC_MEDICAL = "academic-medical"
    #: No ``company_domain`` on file to compare against.
    UNVERIFIABLE = "unverifiable"


_ACADEMIC_RE = re.compile(r"\.(edu|ac\.[a-z]{2,3}|edu\.[a-z]{2,3})$", re.IGNORECASE)

#: Trailing labels that carry no organisational identity, so the registrable stem is the
#: first label left after they are stripped. Small and explicit rather than a public-suffix
#: dependency: this only has to separate `riverbend.edu` from `riverbend.org`, not parse the
#: whole DNS.
_PUBLIC_SUFFIX_PARTS = frozenset(
    {
        "edu",
        "com",
        "org",
        "net",
        "gov",
        "int",
        "ac",
        "co",
        "or",
        "ne",
        "go",
        "uk",
        "sg",
        "au",
        "nz",
        "za",
        "in",
        "jp",
        "hk",
        "my",
        "ca",
        "us",
        # RFC 2606's reserved documentation TLD, which every fixture in this repo uses.
        "example",
    }
)


def _registrable_stem(domain: str) -> str:
    """The identity-bearing label of ``domain`` — ``riverbend`` for both riverbend.edu and
    riverbend.org.

    Used to tell "this .edu IS the account's own domain on another TLD" from "this .edu
    belongs to somebody else entirely", which is the whole difference between a health
    system's real work address and the university-domain-on-a-corporate-row defect.
    """
    labels = [x for x in (domain or "").strip().lower().split(".") if x]
    while len(labels) > 1 and labels[-1] in _PUBLIC_SUFFIX_PARTS:
        labels.pop()
    return labels[-1] if labels else ""


_DOMAIN_ALIASES_FILE = "domain-aliases.toml"


def load_domain_aliases(profile: str, profiles_root: Path | None = None) -> set[frozenset[str]]:
    """Known-benign domain pairs, sourced from the profile's own maintained
    ``knowledge/domain-aliases.toml``.

    ``domain-mismatch`` is right to notice that a contact's email domain diverges from the
    company's own — that is how the university-domain shape was caught. But it is
    also right about a bank's brand domain vs its legal entity's, a print brand vs its
    holding company, a broker vs the trading subsidiary it acquired, a health system vs its
    foundation, and a company vs its own shortened brand domain — five parent/subsidiary and
    brand-vs-legal-name
    pairs across the four re-cut lists, every one benign, every one spending warning budget
    the acting rules need. The rule-lifecycle band for that is explicit: a WARN class that is
    acked every run has not earned its place.

    So the pairs become **data the profile maintains**, exactly like ``competitors.toml``:
    reviewed once, dated in the file, and re-used every run. A missing file simply disables
    the suppression — this module never fabricates a pair, because declaring two domains the
    same company is a fact about the world, not an inference a gate may make.

    Returns a set of unordered pairs, so ``a -> b`` and ``b -> a`` match the same entry.
    """
    root = profiles_root or resolve_profiles_root()
    path = root / profile / "knowledge" / _DOMAIN_ALIASES_FILE
    out: set[frozenset[str]] = set()
    if not path.is_file():
        return out
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    for entry in data.get("alias", []):
        domains = [str(d).strip().lower() for d in (entry.get("domains") or []) if str(d).strip()]
        # An entry naming three domains means all three are the same company, so every
        # pairing among them is benign — not just the first two.
        for i, a in enumerate(domains):
            for b in domains[i + 1 :]:
                out.add(frozenset((a, b)))
    return out


def _domains_aliased(row: dict, aliases: set[frozenset[str]]) -> bool:
    """True when this row's email domain and company domain are a declared alias pair."""
    if not aliases:
        return False
    email_domain = (row.get("email") or "").rsplit("@", 1)[-1].strip().lower()
    company_domain = (row.get("company_domain") or "").strip().lower()
    # `company_domain` is sometimes a URL fragment ("acme.example/us") — take the host only,
    # matching what `merge_hygiene.check_row` compares against.
    company_domain = company_domain.split("/", 1)[0]
    if not email_domain or not company_domain:
        return False
    return frozenset((email_domain, company_domain)) in aliases


def domain_issue(row: dict, aliases: set[frozenset[str]] | None = None) -> tuple[str, str]:
    """Classify a row's email-domain risk by re-running the canonical per-row check
    (:func:`gtm_core.merge_hygiene.check_row`) and re-grading its advisory
    ``email-domain-mismatch`` / ``email-freemail`` findings for a load-time gate.

    Does not re-derive the domain-comparison logic — ``check_row`` already handles it
    correctly (subdomain-aware, blank-``company_domain``-safe). What was missing was a
    moment in the pipeline that re-reads the finding and stops on it before a batch
    loads; this function is that re-grading, not a second implementation. It splits
    the mismatch finding into ACADEMIC (hard stop) vs. a generic MISMATCH (review, not
    a stop) because a flat escalation of every mismatch to ERROR blocked legitimate
    parent/subsidiary and brand-vs-domain rows alongside the real defects.

    Freemail is checked before mismatch on purpose: ``check_row`` raises BOTH findings
    for a freemail address (it is, by definition, also not the company's own domain),
    and PERSONAL is the more useful read of that combination — a founder mailing from
    Gmail is a known, common shape, not evidence of a wrong-entity contact.
    """
    findings = check_row(row)
    freemail = next((f for f in findings if f.rule == "email-freemail"), None)
    if freemail:
        return DomainIssue.PERSONAL, freemail.detail
    mismatch = next((f for f in findings if f.rule == "email-domain-mismatch"), None)
    if mismatch:
        email_domain = (row.get("email") or "").rsplit("@", 1)[-1].strip().lower()
        # ACADEMIC is still resolved BEFORE the general alias suppression below: an .edu
        # contact on a corporate account is never silenced to NONE by an alias file. An
        # alias can only DOWNGRADE it to a warning (see ACADEMIC_MEDICAL), so the finding
        # always survives to a human.
        if _ACADEMIC_RE.search(email_domain):
            company_domain = (row.get("company_domain") or "").strip().lower()
            # Same registrable stem: the .edu is the ACCOUNT'S OWN domain on another TLD
            # (riverbend.edu / riverbend.org), so there is no divergence to report at all. This is
            # a structural fact about the two strings, not a declared alias, which is why
            # it may sit ahead of the hard stop where the alias file may not.
            if company_domain and _registrable_stem(email_domain) == _registrable_stem(
                company_domain
            ):
                return DomainIssue.NONE, ""
            # A DECLARED alias pair downgrades to a warning — it never buys silence.
            # A health system's .edu beside its differently-named .org (the clinic brand and
            # the medical school) are real sibling domains that no structural test can
            # derive, so the operator declares them per pair in domain-aliases.toml and
            # the finding survives as something a human still reads. Judging this from the
            # COMPANY NAME was tried first and rejected: "Health System" in a name would
            # have exempted a stranger's riverbend.edu on an unrelated account, which is
            # the exact wrong-entity defect this rule exists to catch.
            if _domains_aliased(row, aliases or set()):
                return DomainIssue.ACADEMIC_MEDICAL, mismatch.detail
            return DomainIssue.ACADEMIC, mismatch.detail
        if _domains_aliased(row, aliases or set()):
            return DomainIssue.NONE, ""
        return DomainIssue.MISMATCH, mismatch.detail
    if not (row.get("company_domain") or "").strip():
        return DomainIssue.UNVERIFIABLE, "no company_domain on file to check against"
    return DomainIssue.NONE, ""


# --- stale artifact strings ---------------------------------------------------

# A disambiguation note written to tell two same-named entities apart during research
# ("Northwind Parent" — the Northwind Parent Holdings wrapper vs. plain Northwind) that leaked
# untouched into the live `company` merge field instead of being resolved to the real
# name before the row reached a load file.
_STALE_ARTIFACT_RE = re.compile(
    r"""
    \s+(Parent|Subsidiary|Duplicate|Dup|TBD|Unknown|Unverified)\s*$
    | \s*\(\s*(disambiguat\w*|dup(licate)?|not\s+confirmed|see\s+note)[^)]*\)\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def stale_artifact_string(company: str) -> str:
    """Return the offending suffix if ``company`` carries a research-note artifact
    instead of a clean legal/trade name, else ``""``. Flags only — never rewrites the
    field itself, since the correct name is a fact a human confirms, not one this
    module can safely guess.
    """
    m = _STALE_ARTIFACT_RE.search((company or "").rstrip())
    return m.group(0).strip() if m else ""


# --- why_now that records an ABSENCE ------------------------------------------

# `why_now` is the merge field. Whatever it holds is what the prospect reads, so a
# row whose why_now records the *failure* of the sweep — "no dated public why-now
# found this pass" — ships that sentence as the opening line of the email.
#
# This is not hypothetical and it is not caught by anything else. Measured 2026-08-30
# on a 599-row load-ready list: 16 rows carried a negative why_now, and 3 of them sat
# at `verdict: send`. Only one was stopped, incidentally, by `no-dossier` — the other
# two had a dossier on disk and passed every check the gate ran. The verdict column is
# the control that is *supposed* to catch these, and it had a hole: nothing binds a
# verdict to the text of the clause it is a verdict about.
#
# So this fires on the copy, not on the verdict, and it is an ERROR at any verdict. A
# row that admits it has no signal does not belong on a load-ready list whatever the
# verdict says — and if the verdict already says re-angle, this costs nothing because
# the row was never going to load anyway.
_WHY_NOW_ABSENT_RE = re.compile(
    r"""
      no \s+ (qualifying \s+)? dated \s+ public (\s+ \w+)? \s+ (hit|why-?now|signal)
    | dated \s+ public \s+ why-?now \s+ not \s+ (yet \s+)? (confirmed|found)
    | no \s+ (confirmed \s+)? public \s+ (signal|why-?now)
    | firmographic \s* \+ \s* cohort \s+ fit \s+ only
    | no \s+ intent \s+ signal \s+ and \s+ no \s+ confirmed \s+ public \s+ why-?now
    """,
    re.IGNORECASE | re.VERBOSE,
)

# The account was adjudicated unbuyable and the reason was written into why_now. Same
# rendering defect, different cause — kept separate so the operator is told which.
_WHY_NOW_DISQUALIFIED_RE = re.compile(
    r"""
      ^ \s* DISQUALIFIED \b
    | not \s+ a \s+ plausible \s+ (independent \s+ | commercial \s+)? buyer
    | does \s+ not \s+ clear \s+ (the \s+)? publish \s+ floor
    """,
    re.IGNORECASE | re.VERBOSE,
)


def why_now_not_a_signal(why_now: str) -> str:
    """Return why ``why_now`` fails to state a signal, or ``""`` when it states one.

    Two ways a why_now can be about the absence of a signal rather than a signal:
    the sweep came back empty, or the account was disqualified and the reason landed
    in this field. Both render into the email; the caller is told which so the repair
    is obvious (re-research vs. remove the row).
    """
    w = (why_now or "").strip()
    if not w:
        return ""
    if _WHY_NOW_DISQUALIFIED_RE.search(w):
        return "it records a DISQUALIFICATION, not a signal"
    if _WHY_NOW_ABSENT_RE.search(w):
        return "it records that the sweep found NOTHING, not a signal"
    return ""


# --- competitor flag -----------------------------------------------------------

_COMPETITORS_FILE = "competitors.toml"

#: The one tier that is a hard stop. ``competitors.toml``'s own header defines it as
#: "products that overlap the core wedge" — there is no framing that rescues a cold
#: pitch to a company selling the thing you are selling, so this is not a judgement
#: the gate defers to a human. Every other tier (``adjacent``, ``nhi-native``,
#: ``si-channel``) genuinely can be reframed, and stays a WARN.
DIRECT_TIER = "direct"


@dataclass(frozen=True)
class CompetitorHit:
    """One ``competitors.toml`` entry, matched to an account.

    Carries the ``tier`` rather than only the rendered summary because the tier is
    what decides ERROR vs WARN, and re-parsing it back out of a display string is
    how a grading rule silently stops matching the file it grades.
    """

    tier: str
    summary: str

    @property
    def direct(self) -> bool:
        return self.tier.strip().lower() == DIRECT_TIER


def load_competitors(profile: str, profiles_root: Path | None = None) -> dict[str, CompetitorHit]:
    """Map ``org_token`` -> a one-line ``name (tier): note`` summary, sourced from the
    profile's own maintained ``knowledge/competitors.toml`` (schema=1, ``[[competitor]]``
    entries — see the file's header for tier definitions and provenance). Reuses that
    file rather than a second list: it is already reviewed and dated, and a prospected
    account is at least as often named by a product/alias as by the entity the
    watchlist was filed under, so every alias and domain indexes to the same account
    identity as the canonical name.

    Returns an empty dict when the profile ships no such file — this check has
    nothing to compare against; the profile owns the list, this module never
    fabricates one.
    """
    root = profiles_root or resolve_profiles_root()
    path = root / profile / "knowledge" / _COMPETITORS_FILE
    out: dict[str, CompetitorHit] = {}
    if not path.is_file():
        return out
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    for entry in data.get("competitor", []):
        name = (entry.get("name") or "").strip()
        if not name:
            continue
        tier = (entry.get("tier") or "unknown").strip()
        summary = f"{name} ({tier}) — {entry.get('note', '')}".rstrip(" —")
        hit = CompetitorHit(tier=tier, summary=summary)
        domains = entry.get("domains") or [""]
        for n in (name, *entry.get("aliases", [])):
            for d in domains:
                tok = org_token(d, n)
                if tok:
                    out.setdefault(tok, hit)
    return out


def competitor_match(
    company: str, company_domain: str, competitors: dict[str, CompetitorHit]
) -> CompetitorHit | None:
    """Return the matching ``competitors.toml`` entry, or ``None`` if not on the list."""
    tok = org_token(company_domain, company)
    return competitors.get(tok) if tok else None


# --- the audit -----------------------------------------------------------


#: Findings counted per contact row rather than per account. Everything else is
#: deduped to one finding per company.
_ROW_LEVEL_RULES = frozenset(
    {
        "domain-mismatch",
        "domain-personal",
        "domain-unverifiable",
        "verdict-missing",
        "verdict-unknown",
        "verdict-reason-missing",
        "relation-unresolved",
        "relation-competitor",
        "relation-regulator",
        "relation-partner",
        "relation-adjacent",
        "signal-clause-underivable",
        "signal-source-missing",
        "signal-source-malformed",
        "signal-source-is-search",
        "signal-observed-missing",
        "signal-observed-future",
        "signal-stale",
        "signal-evidence-missing",
        "signal-evidence-unsupported",
        "signal-number-unsourced",
        "signal-subject-missing",
        "signal-subject-mismatch",
        "signal-subject-absent-from-evidence",
        "agent-kind-unresolved",
        "agent-kind-unknown",
        "agent-kind-human",
        "agent-kind-contradiction",
        "agent-kind-unused",
    }
)


@dataclass
class AccountAudit:
    rows: int = 0
    accounts: int = 0
    no_dossier: int = 0
    domain_academic: int = 0
    domain_academic_medical: int = 0
    domain_mismatch: int = 0
    domain_personal: int = 0
    domain_unverifiable: int = 0
    stale_artifact: int = 0
    why_now_not_signal: int = 0
    competitor: int = 0
    competitor_direct: int = 0
    leadership_unverified: int = 0
    record_missing_columns: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: Warning classes the operator accepted for this run, by name. Never a blanket
    #: pass — see :mod:`gtm_core.finding_budget`.
    acked: tuple[str, ...] = ()
    budget: int = WARN_BUDGET

    @property
    def warn_verdict(self):
        """The WARN tier graded against the readability budget.

        Domain and record findings are per row; dossier, competitor and leadership
        findings are per account. Reporting both against one denominator produces a
        rate that reads precise and is wrong, so each class is told which population
        it was counted over.
        """
        return budget_verdict(
            self.warnings,
            self.accounts,
            denominators=dict.fromkeys(_ROW_LEVEL_RULES, self.rows),
            acked=self.acked,
            budget=self.budget,
        )

    @property
    def failed(self) -> bool:
        """ERRORs block. So does an unreadable WARN tier, and so does a list that
        predates the research record — neither can be reviewed into a safe send."""
        return bool(self.errors) or bool(self.record_missing_columns) or self.warn_verdict.blocked


@dataclass
class VerdictFilterStats:
    """What :func:`filter_by_verdict` did, so a caller can print the same three lines
    ``main()`` always has — instead of reimplementing the filter and silently omitting
    the calibrated-judge drop, which is how the status dashboard once audited a
    different, larger population than the enrollment gate does."""

    total: int = 0
    kept: int = 0
    judge_dropped: int = 0  # calibrated judge additionally removed
    judge_advisory: int = 0  # uncalibrated judge flagged `drop`, kept anyway


#: Which researcher ``verdict`` values may enrol in each lane (see ``gtm_core.lane_router``).
#: Re-exported, not defined here: the consolidation sweep needs the same rule to filter the
#: hand-send list, and it cannot import this module (this one imports IT). The definition
#: and its full rationale live in :mod:`gtm_core.lane_verdicts`; one rule, one home.
#:
#: Historical note kept with the importer because it is about THIS gate: until 2026-09-03
#: a `re-angle` row reached no sequence at all, because every enrollment path required
#: `send` and had no fallback (461 of 598 pooled rows were stranded).
LANE_VERDICTS = _LANE_VERDICTS
#: Lanes in which a CALIBRATED judge's ``drop`` removes a row. Never generic or repair: the
#: judge read the PERSONALISED body, and its rejection of that argument is the reason the
#: row is in those lanes at all.
_JUDGE_MAY_REMOVE_LANES = frozenset({"", "signal", "personalised"})
#: Findings a GENERIC-lane row carries as ONE aggregate warning per class instead of a
#: per-row ERROR. A generic body references no research, so "no verdict / no relation /
#: no dossier" and research-record findings (signal-*, agent-kind-*) say what the row lacks
#: for a PERSONALISED send, not that this send is unsafe. Every other class — competitor,
#: academic domain, stale artifact — stays ERROR in every lane.
GENERIC_LANE_ADVISORY = frozenset({"no-dossier", "verdict-missing", "relation-unresolved"})


def _is_generic_advisory(rule: str) -> bool:
    return (
        rule in GENERIC_LANE_ADVISORY
        or rule.startswith("signal-")
        or rule.startswith("agent-kind-")
    )


def filter_by_verdict(
    rows: list[dict], want: str, *, lane: str = ""
) -> tuple[list[dict], VerdictFilterStats]:
    """Keep only rows whose researcher ``verdict`` may enrol in ``lane`` (``want`` alone when
    no lane is named — today's behaviour, unchanged), then apply the calibrated-judge-may-
    remove rule: a CALIBRATED judge's ``drop`` removes a row from the signal lane; an
    uncalibrated one is a ranking signal, reported and not obeyed. Outside the signal lane
    a judge ``drop`` is always advisory (see ``_JUDGE_MAY_REMOVE_LANES``). One
    implementation, so ``main()`` and any other caller (the status dashboard) cannot
    report two different counts for the same list.
    """
    want = want.strip().lower()
    lane = lane.strip().lower()
    if lane not in LANE_VERDICTS:
        raise ValueError(
            f"unknown lane {lane!r}; expected one of {sorted(k for k in LANE_VERDICTS if k)}"
        )
    wanted = LANE_VERDICTS[lane] if lane else frozenset({want})
    judge_may_remove = lane in _JUDGE_MAY_REMOVE_LANES
    stats = VerdictFilterStats(total=len(rows))
    kept: list[dict] = []
    for r in rows:
        if (r.get("verdict") or "").strip().lower() not in wanted:
            continue
        if (r.get("judge_verdict") or "").strip().lower() == "drop":
            if judge_may_remove and (r.get("judge_calibrated") or "").strip().lower() == "true":
                stats.judge_dropped += 1
                continue
            stats.judge_advisory += 1
        kept.append(r)
    stats.kept = len(kept)
    return kept, stats


def _demote_generic_lane_findings(a: AccountAudit, lane: str) -> None:
    """Generic lane only: each advisory class becomes ONE aggregate warning.
    Per row they would saturate the WARN budget on exactly the lists the lane
    exists for (300+ un-researched rows) — the same reason ``leadership-freshness``
    aggregates below. Every other lane is returned untouched.
    """
    if lane.strip().lower() != "generic":
        return
    keep: list[str] = []
    demoted: dict[str, int] = {}
    for line in a.errors:
        rule = line.split(":", 1)[0].strip()
        if _is_generic_advisory(rule):
            demoted[rule] = demoted.get(rule, 0) + 1
        else:
            keep.append(line)
    a.errors = keep
    for rule, n in sorted(demoted.items()):
        a.warnings.append(
            f"{rule}: {n} finding(s) — advisory in the GENERIC lane, whose body references "
            f"no research; a personalised send would still block on them"
        )


def audit_rows(
    rows: list[dict],
    profile: str,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
    *,
    fieldnames: list[str] | None = None,
    acked: tuple[str, ...] = (),
    budget: int = WARN_BUDGET,
    as_of: datetime.date | None = None,
    lane: str = "",
) -> AccountAudit:
    """Audit a load-ready prospect list for account-level integrity, immediately
    before it becomes sequence copy — the whole reason this runs after ``list_fit``
    (which judges the list before research spend) and after ``merge_render_linter``
    is designed to run (which judges the rendered copy): this is the last check that
    can still stop a bad row before a human ever sees it as a "ready" email.
    """
    a = AccountAudit(rows=len(rows), acked=acked, budget=budget)
    competitors = load_competitors(profile, profiles_root)
    domain_aliases = load_domain_aliases(profile, profiles_root)

    # The research record. Owned by gtm_core.signal_record; this gate owns the moment
    # it runs — the same arrangement as check_row above, and for the same reason: the
    # finding existed and nothing re-read it before a batch loaded.
    rec = audit_records(
        rows, fieldnames if fieldnames is not None else (list(rows[0]) if rows else []), as_of=as_of
    )
    if rec.missing_columns:
        a.record_missing_columns = rec.missing_columns
    else:
        a.errors.extend(rec.errors)
        a.warnings.extend(rec.warnings)

    # Row-level checks: every contact carries its own email, so these run per row.
    for r in rows:
        company = r.get("company", "")
        issue, detail = domain_issue(r, domain_aliases)
        if issue == DomainIssue.ACADEMIC:
            a.domain_academic += 1
            a.errors.append(
                f"domain-academic: {r.get('email', '')!r} at {company!r} — {detail} — an "
                f"academic address is not a legitimate corporate contact here"
            )
        elif issue == DomainIssue.ACADEMIC_MEDICAL:
            a.domain_academic_medical += 1
            a.warnings.append(
                f"domain-academic-medical: {r.get('email', '')!r} at {company!r} — "
                f"{detail} — declared in domain-aliases.toml as this account's own "
                f"academic domain (an academic medical centre genuinely runs corporate mail "
                f"on .edu); confirm the seat is a buyer and not a clinician"
            )
        elif issue == DomainIssue.MISMATCH:
            a.domain_mismatch += 1
            a.warnings.append(
                f"domain-mismatch: {r.get('email', '')!r} at {company!r} — {detail} — "
                f"often benign (parent/subsidiary, brand vs. legal-name domain); confirm "
                f"before send"
            )
        elif issue == DomainIssue.PERSONAL:
            a.domain_personal += 1
            a.warnings.append(
                f"domain-personal: {r.get('email', '')!r} at {company!r} — free webmail, "
                f"confirm this is really the buyer's working address"
            )
        elif issue == DomainIssue.UNVERIFIABLE:
            a.domain_unverifiable += 1

        why_not = why_now_not_a_signal(r.get("why_now", ""))
        if why_not:
            a.why_now_not_signal += 1
            a.errors.append(
                f"why-now-not-a-signal: {company!r} — {why_not}. why_now is the merge "
                f"field; this row would open its email with that sentence"
            )

        artifact = stale_artifact_string(company)
        if artifact:
            a.stale_artifact += 1
            a.errors.append(
                f"stale-artifact-string: {company!r} carries a research-note suffix "
                f"({artifact!r}) that leaked into the live merge field"
            )

    # Account-level checks: dedupe by org identity so one company with several
    # contacts doesn't produce a repeated dossier/competitor finding per contact.
    seen: set[str] = set()
    for r in rows:
        company = r.get("company", "")
        domain = r.get("company_domain", "")
        tok = org_token(domain, company)
        if not tok or tok in seen:
            continue
        seen.add(tok)
        a.accounts += 1

        has, folder_name = account_has_dossier(profile, company, domain, content_root)
        if not has:
            a.no_dossier += 1
            a.errors.append(f"no-dossier: {company!r} has no research behind its Why Now clause")
        elif classify_dossier_folder(profile, folder_name, content_root) == DossierDepth.BRIEF:
            a.leadership_unverified += 1

        hit = competitor_match(company, domain, competitors)
        if hit and hit.direct:
            a.competitor += 1
            a.competitor_direct += 1
            a.errors.append(
                f"competitor-direct: {company!r} — {hit.summary} — a direct competitor is "
                f"not a framing problem; there is no cold pitch that survives it"
            )
        elif hit:
            a.competitor += 1
            a.warnings.append(f"competitor-flag: {company!r} — {hit.summary}")

    # Two checks whose finding is the SAME sentence with a different name in it, and
    # which by construction fire on most of a bulk run: the dossier variant is chosen
    # once for the whole list, and a provider export either carries company_domain or
    # does not. Emitted per account they saturate the WARN tier at 80-95% and bury the
    # 1-3% classes that actually discriminate (2026-08-19: leadership-freshness alone
    # was 82 of the 93 warnings on one list). One aggregate finding says the same thing,
    # costs 1 against the readability budget instead of 82, and states the coverage as
    # a rate — which is the form that shows it is a decision about the run, not news
    # about an account.
    if a.leadership_unverified:
        a.warnings.append(
            f"leadership-freshness: {a.leadership_unverified}/{a.accounts} account(s) have "
            f"only the prospecting-brief dossier, which by design skips the fresh "
            f"leadership re-check — a departure, a death, or a reorg on any of them would "
            f"not have been caught. Read the ones where that matters before enrolling."
        )
    if a.domain_unverifiable:
        a.warnings.append(
            f"domain-unverifiable: {a.domain_unverifiable}/{a.rows} row(s) carry no "
            f"company_domain, so the domain check could not run on them at all — a "
            f"skipped check, not a passed one"
        )

    _demote_generic_lane_findings(a, lane)
    return a


def render(a: AccountAudit) -> str:
    lines = [f"account-integrity audit — {a.rows} row(s), {a.accounts} account(s)", ""]
    if a.record_missing_columns:
        # One file-level finding, and the OTHER checks still run and still print. A
        # migration that also blinds the dossier/domain/competitor checks would leave
        # the operator worse off than before the record existed.
        lines.append(
            "  ERROR — this list predates the research record; no per-row provenance "
            "or verdict was checked. Missing column(s): " + ", ".join(a.record_missing_columns)
        )
        lines.append(
            "  Re-run the `prospect` skill's research step to populate them. Not "
            "repairable by editing the CSV — the fields record what research found, "
            "and inventing them is the defect they exist to catch."
        )
        lines.append("")
    lines.append(f"  no-dossier:            {a.no_dossier}")
    lines.append(f"  domain-academic:       {a.domain_academic}")
    lines.append(f"  domain-academic-med:   {a.domain_academic_medical}")
    lines.append(f"  domain-mismatch:       {a.domain_mismatch}")
    lines.append(f"  domain-personal:       {a.domain_personal}")
    lines.append(f"  domain-unverifiable:   {a.domain_unverifiable}  (no company_domain on file)")
    lines.append(f"  why-now-not-a-signal:  {a.why_now_not_signal}")
    lines.append(f"  stale-artifact-string: {a.stale_artifact}")
    lines.append(f"  competitor:            {a.competitor}  ({a.competitor_direct} direct — ERROR)")
    lines.append(f"  leadership-freshness:  {a.leadership_unverified}")
    lines.append("")
    if a.errors:
        lines.append(f"  ERRORS — {len(a.errors)} finding(s). Do not load until resolved:")
        lines.extend(f"    - {e}" for e in a.errors)
        lines.append("")
    v = a.warn_verdict
    if a.warnings:
        lines.append(render_budget(v, unit="account", units=dict.fromkeys(_ROW_LEVEL_RULES, "row")))
        if v.enumerable:
            lines.extend(f"    - {w}" for w in a.warnings)
        lines.append("")
    if not a.errors and not a.warnings:
        lines.append("  PASS — no account-integrity findings.")
    lines.append("FAIL" if a.failed else "PASS")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="gtm_core.account_integrity",
        description=(
            "Pre-load account gate: dossier coverage, domain integrity, stale research "
            "artifacts, competitor conflicts, leadership-freshness, and the row's "
            "research record (source/date/evidence/subject/agent-kind/relation/verdict). "
            "Warning output is budgeted: past --budget unacknowledged findings it reports "
            "rates instead of a list, and blocks."
        ),
    )
    p.add_argument("--csv", required=True, type=Path, help="the prospect list about to be loaded")
    p.add_argument(
        "--profile", required=True, help="active profile (reads its accounts/ + knowledge/)"
    )
    # One suppression semantics across `list_fit`, this gate, and `merge_render_linter`:
    # consulting the ledger is the DEFAULT. They previously disagreed — one always
    # skipped, two were opt-in — so whether a suppressed person was linted depended on
    # which gate ran, and their findings crowded out the rows that would actually send.
    p.add_argument(
        "--include-suppressed",
        action="store_true",
        help="lint suppressed rows too (default: skip them — they will never be sent)",
    )
    p.add_argument(
        "--skip-suppressed",
        action="store_true",
        help="deprecated, now the default; accepted so existing invocations keep working",
    )
    p.add_argument("--warn-only", action="store_true", help="report findings but exit 0")
    p.add_argument(
        "--ack",
        action="append",
        default=[],
        metavar="RULE",
        help=(
            "accept one WARN class for this run, by rule name (repeatable). Per class "
            "on purpose: a blanket acknowledgment of 388 findings is not a decision "
            "anyone made."
        ),
    )
    p.add_argument(
        "--budget",
        type=int,
        default=WARN_BUDGET,
        help=(
            f"how many unacknowledged warnings may be enumerated before the gate "
            f"reports rates and blocks (default {WARN_BUDGET})"
        ),
    )
    p.add_argument(
        "--as-of",
        type=datetime.date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="pin today's date for the signal-freshness check (tests, replays)",
    )
    p.add_argument(
        "--require-verdict",
        default="",
        metavar="VERDICT",
        help=(
            "keep only rows carrying this verdict (normally `send`). Use at enrollment: "
            "a re-angle or drop row reaching the sequencer is the defect the verdict exists "
            "to prevent."
        ),
    )
    p.add_argument(
        "--lane",
        default="",
        # Derived, never typed: the enrollable lanes are exactly the keys of LANE_VERDICTS.
        # `personalised` was missing from a hand-written list until 2026-09-09, so the
        # router's own best lane could not be named at the gate — `--lane signal` was
        # refused on the lane-column check and the operator's only route was to omit the
        # flag, which skips that check for every list, not just this one.
        choices=sorted(LANE_VERDICTS),
        help=(
            "which lane this list is being enrolled into (gtm_core.lane_router). Widens "
            "--require-verdict to the lane's admissible verdicts and, for `generic`, reports "
            "no-dossier/verdict-missing/relation-unresolved as one advisory line each. A CSV "
            "whose own `lane` column disagrees is refused: a list routed into one lane must "
            "not be enrolled into another."
        ),
    )
    args = p.parse_args(argv)
    lane = args.lane.strip().lower()

    with args.csv.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if args.require_verdict:
        want = args.require_verdict.strip().lower()
        refusal, lane = check_enrollment_lanes(rows, args.profile, lane, fieldnames, want=want)
        if refusal:
            print(refusal, file=sys.stderr)
            return 2
        refusal = check_account_status(rows, args.profile)
        if refusal:
            print(refusal, file=sys.stderr)
            return 2
    else:
        if lane and "lane" in fieldnames:
            foreign = sorted({(r.get("lane") or "").strip().lower() for r in rows} - {lane, ""})
            if foreign:
                print(
                    f"REFUSED: --lane {lane!r} but the CSV's own lane column carries {foreign} — "
                    f"a list routed into one lane must not be enrolled into another",
                    file=sys.stderr,
                )
                return 2
        if "lane" in fieldnames:
            refusal = _refuse_lane_state_mismatch(rows, args.profile)
            if refusal:
                print(refusal, file=sys.stderr)
                return 2
    if not args.include_suppressed:
        before = len(rows)
        index = load_suppression_index(suppression_ledger(args.profile))
        rows = [r for r in rows if not (r.get("suppression") or "").strip() and not index.match(r)]
        if before != len(rows):
            print(f"suppressed: skipped {before - len(rows)} row(s) (ledger + column)\n")
    if args.require_verdict:
        want = args.require_verdict.strip().lower()
        rows, vstats = filter_by_verdict(rows, want, lane=lane)
        admitted = "/".join(sorted(v or "(empty)" for v in LANE_VERDICTS[lane])) if lane else want
        print(
            f"verdict filter: kept {vstats.kept}/{vstats.total} row(s) with verdict in "
            f"{admitted!r}" + (f" (lane {lane})" if lane else "")
        )
        if vstats.judge_dropped:
            print(f"  judge (calibrated) additionally removed {vstats.judge_dropped} row(s)")
        if vstats.judge_advisory:
            print(
                f"  judge flagged {vstats.judge_advisory} row(s) `drop` but is NOT calibrated — "
                f"kept, and reported below. Seal a holdout to make these binding."
            )
        print()

    a = audit_rows(
        rows,
        args.profile,
        fieldnames=fieldnames,
        acked=tuple(args.ack),
        budget=args.budget,
        as_of=args.as_of,
        lane=lane,
    )
    print(render(a))
    return 0 if (args.warn_only or not a.failed) else 1


if __name__ == "__main__":
    sys.exit(main())
