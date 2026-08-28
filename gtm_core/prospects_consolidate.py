"""Fold every un-consolidated prospect-run export into one deliverability-gated,
DNC-clean master list.

Each prospect run emits its own ``prospects-<date>-<cohort>-hubspot.csv`` (see
:mod:`gtm_core.prospects_import`), but folding those into a loadable sequencer
list has always been a manual end-of-flow step — one the operator often can't
reach because a run gets interrupted or the flow is only run partway. Exports
then pile up un-consolidated and silently invisible: ``latest.json`` is
account/dashboard-level and never holds person+email rows, so nothing surfaces
the backlog. On 2026-07-23 that gap hid 370 real emails across 25+ exports
(and 26 live DNC violations inside them) behind a stale, hand-built master list.

This module is the plumbing half of the fix — it is deliberately MCP-free
(stdlib-only, matching the rest of gtm_core) so it can run standalone, on a
schedule, or mid-flow after a partial run. The judgment/MCP half (fetching the
*live* Saleshandy DNC list) belongs to the skill/agent layer, which dumps it to
a small cache file first — see ``dnc_cache_path`` below.

One-file-for-humans layout (the "make it simple" requirement): the operator was
drowning in near-identical lists. So there is exactly **one** file a human ever
looks at — ``sequences/ready-to-load.csv`` (the load-me list) — and everything
else (the full ``master-list.csv`` audit trail, the ``needs-verification.csv``
hold queue, snapshots, the blocked log) lives in a hidden ``sequences/.pool/``
dir. The mental model is then literally what the folder shows: one file.

Person-level identity, not just email (the "don't re-burn a contact" fix): the
email-only DNC/sent filter misses the same person re-resolved under a second
address (``sam@vertex.example`` on DNC, ``sortega@vertex.example`` in a later run) and
double-counts one person carrying two email formats (``robin.kraft@`` +
``rkraft@``). So the loadable outputs are additionally deduped by a person key
(normalized first+last + org token from the domain/company) — a row is dropped
if that person was already sent under *any* address, and same-person duplicates
collapse to the highest-confidence one. The full ``master-list.csv`` keeps every
address for audit; only the loadable files are person-unique.

Deliverability gate (the "make it the default" requirement): an email with no
verification signal is exactly the kind of unknown that tanks bounce rate
during mailbox ramp-up (see the ramp-up lessons in email-sequence skill docs).
So a row is classified into a confidence tier from whatever ``email_status``/
``conf`` signal its source export carried (RocketReach grade, "verified",
"account-folder-verified", or nothing at all):

  * ``high``    — RocketReach A/A-, "verified", "account-folder-verified"
                  -> written to ``ready-to-load.csv`` (safe to load today)
  * ``medium``  — RocketReach B, "found", ``conf`` in (high, med) with no grade
                  -> written to ``needs-verification.csv`` (verify before load)
  * ``unknown`` — no signal at all (the common case for a fresh raw export)
                  -> also ``needs-verification.csv``
  * ``blocked`` — RocketReach F(pattern), ``conf`` "invalid"
                  -> excluded from every loadable file, logged to
                  ``.blocked-log.jsonl`` for audit, never silently dropped

Only ``high`` confidence rows land in ``ready-to-load.csv`` by default.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from gtm_core.merge_hygiene import (
    bare_host,
    clean_company,
    clean_first_name,
    clean_last_name,
    clean_title,
    signal_clause,
    signal_is_fresh,
)
from gtm_core.merge_hygiene import blocks as mh_blocks
from gtm_core.merge_hygiene import check_row as mh_check_row
from gtm_core.paths import _safe_segment, resolve_content_root
from gtm_core.prospects_state import (
    ACCOUNT_ID_FIELD,
    _identity_key,
    _identity_keys,
    load_latest,
)
from gtm_core.prospects_state import _norm as _norm_company

from . import prospect_paths
from .email_compliance import normalize_market, read_target_markets
from .signal_record import JUDGE_COLUMNS, RECORD_COLUMNS, SIGNAL_COLUMN
from .slugify import slug
from .suppression import load_index as load_suppression_index

# --- schema -----------------------------------------------------------------

MASTER_COLS = [
    "first",
    "last",
    "email",
    "title",
    "company",
    "company_domain",
    "city",
    "country",
    "segment",
    "tier",
    "score",
    "conf",
    "email_status",
    "why_now",
    "case_study",
    "src",
    "conf_tier",
    # Buyer-intent / ICP-cohort signal, carried through from the hubspot CSV so it
    # survives consolidation instead of being dropped. Appended (not inserted) to
    # preserve column-position compatibility with anything reading by index.
    "heat",
    "top_intent_score",
    "intent_topics",
    "cohort",
    "qualification_path",
    # The research record (:mod:`gtm_core.signal_record`) — provenance for the Why Now
    # clause plus the send/re-angle/drop verdict. Appended for the same reason as the
    # intent block above: the projection at `_read_master` is a hard `{c: r.get(c)}`
    # over this list, so a column absent from it is silently dropped on the next sweep.
    # The clause used to be the row's entire claim to being researched, which made a
    # renamed company, a fact about the wrong entity, and a number that had drifted
    # from its source all undetectable by construction.
    *RECORD_COLUMNS,
    # The matrix SIGNAL this row's own why-now attests (:data:`gtm_core.signal_record.
    # SIGNAL_COLUMN`) -- appended for the identical reason as the record block above: the
    # `_load_master` projection is a hard `{c: r.get(c)}` over this list, so a column absent
    # from it is silently dropped on the next sweep. Not derived here -- `gtm_core.
    # hook_coverage.derive_row_cell` is the one place a row's persona x segment x signal
    # becomes a cell, and it reads this column plus `title`/`segment`, both already above.
    SIGNAL_COLUMN,
    # The suppression cache (:mod:`gtm_core.suppression`). Absent from this list until
    # 2026-08-27, which is precisely why every sweep destroyed it: `suppression apply`
    # wrote the two columns, the next `_load_master` projection dropped them, and the
    # exclusions survived only in the ledger — where nothing in the build was reading
    # them. That made "re-run apply after every consolidate" a step a human had to
    # remember, and on 2026-08-11 nobody did: 55 exclusions were wiped inside an hour.
    # Carrying them here plus stamping from the ledger below makes it structural.
    "suppression",
    "suppression_date",
    # The judge's own verdict columns (:data:`gtm_core.signal_record.JUDGE_COLUMNS`),
    # appended for the same projection reason as every block above. Without them the
    # judged CSV was an orphan: `email-quality judge` wrote verdicts into its own output
    # file while the enrollment gate read `ready-to-load.csv`, which consolidate
    # regenerates — so nothing carried a judgment from where it was made to where it was
    # enforced. Kept separate from `verdict`, which is the researcher's.
    *JUDGE_COLUMNS,
    # Stamped identity, appended last. `pool_row_id` names this person-row; `account_id`
    # is
    # the account's id from latest.json, the ledger of record. Six mutually
    # non-derivable identity derivations existed across this pipeline, so joining two
    # files meant re-deriving a key from fields one of them might not carry — which is
    # how a prospect's status, suppression, and research ended up under keys that could
    # not find each other. The derivations still ASSIGN identity; these carry it.
    #
    # Named `pool_row_id`, NOT `row_id`: `gtm_core.adjudication.Adjudication.row_id` is
    # already an eval-sheet identity at a different grain — sha256(spec|csv|email|touch),
    # one per TOUCH. Two different facts under one name is the defect class this whole
    # change is about, so the pool's row identity gets its own name.
    "pool_row_id",
    ACCOUNT_ID_FIELD,
]

# Canonical field -> header variants seen across hubspot exports + the flat
# master-list schema. The GTM_* prefix varies by which pack generated the run.
_ALIASES = {
    "first": ("First Name", "first"),
    "last": ("Last Name", "last"),
    "name": ("Contact Name",),  # split into first/last when those are absent
    "email": ("Email", "Contact Email", "email"),
    "title": ("Job Title", "Contact Title", "title"),
    "company": ("Company Name", "Company", "company"),
    "company_domain": ("Company Domain Name", "company_domain"),
    "city": ("City", "HQ City", "city"),
    "country": ("Country/Region", "Market", "country"),
    "segment": ("GTM_Segment", "Segment", "segment"),
    "tier": ("GTM_Tier", "Tier", "tier"),
    "score": ("GTM_Score", "Score", "Lead Score", "score"),
    "conf": ("conf",),
    "email_status": ("Email Status", "email_status"),
    "why_now": ("GTM_Why_Now", "Why Now", "why_now"),
    "case_study": ("GTM_Case_Study", "Case Study", "case_study"),
    "heat": ("GTM_Heat", "Heat", "heat"),
    "top_intent_score": ("GTM_Top_Intent_Score", "top_intent_score"),
    "intent_topics": ("GTM_Intent_Topics", "Intent Topics", "intent_topics"),
    "cohort": ("GTM_Persona_Tier", "Cohort", "cohort"),
    "qualification_path": ("GTM_Qualification_Path", "Qualification Path", "qualification_path"),
    # The research record + the researcher's verdict. Absent from this map until
    # 2026-08-27, which made the documented handoff impossible: `prospect` is told to
    # write the record into its export, `_get` returns "" for any field with no alias,
    # and the row mapping therefore produced 22 of the 38 master columns. The record
    # could only ever reach the pool by being written straight into master-list.csv, so a
    # run that followed the skill exactly still produced the "list predates the record
    # columns" file-level ERROR at the enrollment gate.
    "signal_source_url": ("GTM_Signal_Source_URL", "Signal Source URL", "signal_source_url"),
    "signal_observed": ("GTM_Signal_Observed", "Signal Observed", "signal_observed"),
    "signal_evidence": ("GTM_Signal_Evidence", "Signal Evidence", "signal_evidence"),
    "signal_subject": ("GTM_Signal_Subject", "Signal Subject", "signal_subject"),
    "signal_agent_kind": ("GTM_Signal_Agent_Kind", "Signal Agent Kind", "signal_agent_kind"),
    "category_relation": ("GTM_Category_Relation", "Category Relation", "category_relation"),
    "verdict": ("GTM_Verdict", "Verdict", "verdict"),
    "verdict_reason": ("GTM_Verdict_Reason", "Verdict Reason", "verdict_reason"),
    SIGNAL_COLUMN: ("GTM_Signal_Column", "Signal Column", SIGNAL_COLUMN),
}

# Apollo's `email_status` / `contact_email_status` vocabulary is exactly four values —
# `verified`, `unverified`, `likely to engage`, `unavailable` (docs.apollo.io People API
# Search, verified 2026-07-27). Match those literally. An earlier version of this line
# guessed the labels ("apollo likely", "apollo unavailable"); those strings never occur, so
# three of Apollo's four statuses fell through to `unknown` and every such contact was
# silently routed to the hidden hold queue instead of ready-to-load.csv — the same failure
# this regex set exists to prevent.
_HIGH_STATUS = re.compile(r"rocketreach a|^(apollo )?verified$|account-folder-verified", re.I)
_MEDIUM_STATUS = re.compile(
    r"rocketreach b|likely to engage|^(apollo )?unverified$|^found$|^likely$", re.I
)
_BLOCKED_STATUS = re.compile(r"rocketreach f|^(apollo )?unavailable$|pattern|^invalid$", re.I)


def _get(row: dict, field: str) -> str:
    for key in _ALIASES.get(field, ()):
        v = (row.get(key) or "").strip()
        if v:
            return v
    return ""


def _score_num(s: str) -> int:
    """``"7/12"`` -> ``7``; ``"11"`` -> ``11``; unparseable -> ``-1``."""
    s = (s or "").split("/")[0].strip()
    try:
        return int(float(s))
    except ValueError:
        return -1


def classify_confidence(email_status: str, conf: str) -> str:
    """Deliverability confidence tier from whatever verification signal a row
    carries. No signal at all (the common case for a fresh raw export) is
    ``"unknown"`` — treated as NOT safe to load by default, not as a free pass.
    """
    status = (email_status or "").strip()
    c = (conf or "").strip().lower()
    if _BLOCKED_STATUS.search(status) or c == "invalid":
        return "blocked"
    if _HIGH_STATUS.search(status):
        return "high"
    if _MEDIUM_STATUS.search(status) or c in ("high", "med"):
        return "medium"
    return "unknown"


_CONF_RANK = {"high": 3, "medium": 2, "unknown": 1, "blocked": 0}


def org_token(company_domain: str, company: str) -> str:
    """A stable per-company token that reconciles a domain and a bare name —
    ``vertex.example`` and ``"Vertex"`` both collapse to ``vertex`` — so the same person
    (or account, in :mod:`gtm_core.account_integrity`) matches across a run that carried
    a domain and one that carried only a name. Public: the canonical account-identity
    primitive for this pipeline — reuse it rather than re-deriving a company token.
    """
    # Same host normaliser the hygiene gate compares on, so an account cannot get two
    # identities because one row stored "acme.com" and another "https://acme.com/en".
    # A path happened to survive the old code (split(".")[0] cut before it), but a
    # scheme did not — "https://acme.com" tokenised to "https://acme".
    domain = bare_host(company_domain)
    if domain:
        return domain.split(".")[0]
    return _norm_company(company or "").replace(" ", "")


def _person_key(rec: dict) -> str:
    """Identity of a *human* across email-format changes: normalized first+last
    plus the org token. Empty when there isn't enough to identify a person (no
    name, or no org) — an empty key is never collapsed or excluded, only kept.
    """
    first = re.sub(r"[^a-z0-9]", "", (rec.get("first") or "").lower())
    last = re.sub(r"[^a-z0-9]", "", (rec.get("last") or "").lower())
    org = org_token(rec.get("company_domain", ""), rec.get("company", ""))
    if (not first and not last) or not org:
        return ""
    return f"{first}|{last}|{org}"


def _row_to_record(row: dict, src: str) -> dict:
    first, last = _get(row, "first"), _get(row, "last")
    if not first and not last and _get(row, "name"):
        parts = _get(row, "name").split()
        first = parts[0] if parts else ""
        last = " ".join(parts[1:]) if len(parts) > 1 else ""
    email = _get(row, "email").lower()
    company = _get(row, "company")
    # Repair the merge fields at ingestion, so a scraped LinkedIn headline or an
    # emoji-in-the-first-name never reaches ready-to-load.csv and gets substituted
    # into "Hi <first>," at send time. Conservative by construction: an unrepairable
    # value comes back unchanged and is caught by the ready-list gate below.
    # Order matters: first is derived from the RAW last (the swapped-field recovery),
    # so clean last only afterwards.
    first = clean_first_name(first, last, email)
    last = clean_last_name(last)
    company = clean_company(company)
    email_status = _get(row, "email_status")
    conf = _get(row, "conf")
    return {
        "first": first,
        "last": last,
        "email": email,
        "title": clean_title(_get(row, "title")),
        "company": company,
        "company_domain": _get(row, "company_domain"),
        "city": _get(row, "city"),
        "country": _get(row, "country"),
        "segment": _get(row, "segment"),
        "tier": _get(row, "tier"),
        "score": _get(row, "score"),
        "conf": conf,
        "email_status": email_status,
        "why_now": _get(row, "why_now"),
        "case_study": _get(row, "case_study"),
        "src": src,
        "conf_tier": classify_confidence(email_status, conf),
        "heat": _get(row, "heat"),
        "top_intent_score": _get(row, "top_intent_score"),
        "intent_topics": _get(row, "intent_topics"),
        "cohort": _get(row, "cohort"),
        "qualification_path": _get(row, "qualification_path"),
        # The research record and the researcher's verdict, read from the export the
        # `prospect` skill writes. Judge columns and the stamped ids are deliberately
        # NOT here: those are assigned downstream, and letting a source export set them
        # would let an input claim it had already been judged.
        **{col: _get(row, col) for col in (*RECORD_COLUMNS, SIGNAL_COLUMN)},
    }


# --- IO -----------------------------------------------------------------


def _prospects_dir(profile: str, content_root: Path | None = None) -> Path:
    root = content_root or resolve_content_root()
    # ``profile`` reaches here straight from --profile; guard it as a bare segment
    # before it is joined (CLAUDE.md tenant boundary).
    return root / _safe_segment(profile, "profile") / "prospects"


def _sequences_dir(profile: str, content_root: Path | None = None) -> Path:
    return _prospects_dir(profile, content_root) / "sequences"


def _pool_dir(profile: str, content_root: Path | None = None) -> Path:
    """Hidden home for everything that isn't the one human-facing load file:
    the full master-list audit, the needs-verification hold queue, snapshots,
    and the blocked log. Keeps ``sequences/`` down to a single visible CSV."""
    return _sequences_dir(profile, content_root) / ".pool"


def ready_to_load_path(profile: str, content_root: Path | None = None) -> Path:
    """The ONE file a human (or the email-sequence skill) loads from."""
    return _sequences_dir(profile, content_root) / "ready-to-load.csv"


def needs_verification_path(profile: str, content_root: Path | None = None) -> Path:
    return _pool_dir(profile, content_root) / "needs-verification.csv"


def dnc_cache_path(profile: str, content_root: Path | None = None) -> Path:
    """Where the skill/agent layer should dump the live Saleshandy DNC entries
    before calling :func:`consolidate` — this module never calls the Saleshandy MCP
    itself (plumbing vs. judgment split).

    Two accepted shapes. Preferred::

        {"emails": [...], "domains": [...], "fetched_at": "<ISO-8601 UTC>"}

    Legacy (still read, for caches written before domains were supported)::

        ["addr@example.com", ...]

    A legacy bare list carries no ``fetched_at``, so it can never satisfy a staleness
    check — under ``require_dnc`` it is rejected as un-datable rather than trusted.
    """
    return _prospects_dir(profile, content_root) / ".cache" / "dnc-emails.json"


# How old a suppression cache may be before ``require_dnc`` refuses it. Suppression
# data going stale is silent under-suppression — a compliance failure, not a warning —
# so the scheduled path re-fetches every run and this is only the backstop.
DNC_MAX_AGE_HOURS = 24


class DncSuppression:
    """The suppression set: opted-out emails plus whole blocked domains.

    Saleshandy DNC entries are ``email`` **or** ``domain`` typed, and a domain entry
    suppresses every address at that domain. An email-only check silently under-blocks
    the moment someone adds a domain entry in the provider UI, so both are carried here
    and every call site asks :meth:`blocks` rather than testing membership itself.
    """

    __slots__ = ("emails", "domains", "fetched_at")

    def __init__(
        self,
        emails: set[str] | None = None,
        domains: set[str] | None = None,
        fetched_at: datetime | None = None,
    ) -> None:
        self.emails = emails or set()
        self.domains = domains or set()
        self.fetched_at = fetched_at

    def __len__(self) -> int:
        return len(self.emails) + len(self.domains)

    def blocks(self, email: str, company_domain: str = "") -> bool:
        """True if this address is suppressed — by exact address, by the domain of the
        address itself, or by the row's company domain."""
        email = email.strip().lower()
        if email in self.emails:
            return True
        if not self.domains:
            return False
        candidates = {company_domain.strip().lower().removeprefix("www.")}
        if "@" in email:
            candidates.add(email.rsplit("@", 1)[1])
        return any(c and c in self.domains for c in candidates)


class MarketGate:
    """The allowed-jurisdiction filter: a row's country must be inside ``target_markets``.

    Email law is not the same in every market (US CAN-SPAM needs no prior consent; Canada and
    Australia are consent-based; the EU/UK need a documented lawful basis), so a lead in a market
    the sequence was not built for is a compliance problem *before* it is a targeting one. Gating
    here — at consolidation — means such a lead never reaches ``ready-to-load.csv``, and so is never
    the thing an operator has to remember to strip at enrollment time.

    A **blank** country is *unresolved*, not *allowed*: it is counted separately and, under
    ``strict``, dropped. Non-strict is the default because a partly-enriched pool would otherwise
    empty itself; the count is always reported so the gap stays visible.

    A literal ``"global"`` entry (case-insensitive, after quote-stripping) is a wildcard: it is a
    profile's explicit declaration that it has no jurisdiction restriction, so it disables the
    filter entirely rather than being compared against a row's country like any other market — a
    row's country is never literally the word "global", so without this special case a profile
    could list "global" and still have every row excluded.
    """

    __slots__ = ("allowed", "markets", "strict", "wildcard")

    def __init__(self, markets: list[str] | None = None, *, strict: bool = False) -> None:
        self.markets = markets or []
        normalized = {normalize_market(m) for m in self.markets}
        self.wildcard = "global" in normalized
        self.allowed = normalized
        self.strict = strict

    def __bool__(self) -> bool:
        """False when no markets resolved — an unconfigured gate filters nothing."""
        return bool(self.allowed)

    def blocks(self, country: str) -> bool:
        """True if this row must not be loaded. Blank country blocks only under ``strict``."""
        if self.wildcard:
            return False
        c = (country or "").strip()
        if not c:
            return self.strict
        return normalize_market(c) not in self.allowed


def _resolve_market_gate(profile: str, strict: bool) -> MarketGate:
    """Read ``target_markets`` from the profile; an unreadable profile leaves the gate off.

    Off is loud, not silent — ``consolidate`` reports ``market_gate`` in its result and warns on
    stderr — because the hard stop for a real send is the ``gtm_core.email_compliance`` preflight,
    which fails closed. This gate is the earlier, cheaper net.
    """
    try:
        return MarketGate(read_target_markets(profile), strict=strict)
    except (FileNotFoundError, ValueError) as exc:
        print(f"market gate off ({exc}) — pool not filtered by jurisdiction", file=sys.stderr)
        return MarketGate()


def _parse_fetched_at(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _load_dnc(
    profile: str,
    content_root: Path | None,
    dnc_file: Path | None,
    *,
    require: bool = False,
    max_age_hours: int = DNC_MAX_AGE_HOURS,
) -> DncSuppression:
    """Load the suppression cache.

    Default (``require=False``) preserves the permissive interactive behaviour: a
    missing cache yields an empty suppression set. ``require=True`` — which every
    scheduled/unattended run passes — makes a missing, empty, undatable, or stale
    cache a **hard error**. Consolidating with an empty suppression set is
    indistinguishable in its output from consolidating with a correct one, which is
    exactly why it must not be reachable by accident.
    """
    path = dnc_file or dnc_cache_path(profile, content_root)
    if not path.exists():
        if require:
            raise ValueError(
                f"DNC suppression cache missing: {path}. Refusing to consolidate — an "
                "absent cache silently suppresses nothing. Refresh it from the live "
                "provider list (saleshandy list_dnc_lists -> get_dnc_items) first."
            )
        return DncSuppression()

    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):  # legacy bare-list cache
        payload: dict = {"emails": raw, "domains": [], "fetched_at": ""}
    else:
        payload = raw

    dnc = DncSuppression(
        emails={e.strip().lower() for e in payload.get("emails") or [] if str(e).strip()},
        domains={
            d.strip().lower().removeprefix("www.")
            for d in payload.get("domains") or []
            if str(d).strip()
        },
        fetched_at=_parse_fetched_at(payload.get("fetched_at") or ""),
    )

    if not require:
        return dnc
    if not dnc:
        raise ValueError(
            f"DNC suppression cache is empty: {path}. Refusing to consolidate — refresh "
            "it from the live provider list, or confirm the list really is empty."
        )
    if dnc.fetched_at is None:
        raise ValueError(
            f"DNC suppression cache has no usable `fetched_at`: {path}. Refusing to "
            "consolidate — an undatable cache cannot be shown to be current."
        )
    age_h = (datetime.now(UTC) - dnc.fetched_at).total_seconds() / 3600
    if age_h > max_age_hours:
        raise ValueError(
            f"DNC suppression cache is stale: {path} fetched {age_h:.1f}h ago "
            f"(max {max_age_hours}h). Refusing to consolidate against stale suppression "
            "data — refresh it from the live provider list first."
        )
    return dnc


def _load_sent(profile: str, content_root: Path | None) -> tuple[set[str], set[str]]:
    """Everyone already contacted, by ``sequences/*.csv`` rows marked ``SENT`` —
    covers manual sends that predate a formal Saleshandy sequence/DNC entry.
    Returns ``(emails, person_keys)``: the person keys catch the same human
    re-resolved under a second address (the email set alone can't)."""
    emails: set[str] = set()
    people: set[str] = set()
    seq_dir = _sequences_dir(profile, content_root)
    for path in sorted(seq_dir.glob("*.csv")):
        with path.open(newline="", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None or "status" not in reader.fieldnames:
                continue
            for row in reader:
                if (row.get("status") or "").strip().upper() != "SENT":
                    continue
                email = (row.get("email") or "").strip().lower()
                if email:
                    emails.add(email)
                pk = _person_key(_row_to_record(row, path.name))
                if pk:
                    people.add(pk)
    return emails, people


def _existing_master_path(profile: str, content_root: Path | None) -> Path | None:
    """The canonical rolling file if it already exists; otherwise the newest
    dated ``master-list*.csv`` on disk (so a first run seeds from whatever the
    operator built by hand). Never assumes a fixed prior filename."""
    pool = _pool_dir(profile, content_root)
    canonical = pool / "master-list.csv"
    if canonical.exists():
        return canonical
    seq_dir = _sequences_dir(profile, content_root)
    # Migration: seed from the pre-.pool location (or any hand-built dated file)
    # the first time we run under the new layout.
    candidates = sorted(seq_dir.glob("master-list*.csv"), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def _load_master(path: Path | None) -> list[dict]:
    if path is None or not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="ignore") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        email = (r.get("email") or "").strip().lower()
        if not email:
            continue
        rec = {c: (r.get(c) or "") for c in MASTER_COLS}
        rec["email"] = email
        # Repair merge fields on load as well as on ingestion, so the fix is retroactive:
        # rows folded into the master list before this gate existed are cleaned on the next
        # sweep rather than staying dirty forever. Idempotent, so re-running is a no-op.
        rec["first"] = clean_first_name(rec["first"], rec["last"], email)
        rec["last"] = clean_last_name(rec["last"])
        rec["company"] = clean_company(rec["company"])
        rec["title"] = clean_title(rec["title"])
        if not rec["conf_tier"]:
            rec["conf_tier"] = classify_confidence(rec["email_status"], rec["conf"])
        out.append(rec)
    return out


def _atomic_write_csv(path: Path, rows: list[dict]) -> None:
    _atomic_write_csv_cols(path, rows, MASTER_COLS)


def _atomic_write_csv_cols(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=columns)
            w.writeheader()
            w.writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _snapshot(path: Path, snap_dir: Path, keep: int = 30) -> Path | None:
    if not path.exists():
        return None
    snap_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S-%fZ")
    dest = snap_dir / f"master-list-{stamp}.csv"
    dest.write_bytes(path.read_bytes())
    snaps = sorted(snap_dir.glob("master-list-*.csv"))
    for old in snaps[:-keep]:
        old.unlink(missing_ok=True)
    return dest


def _append_blocked_log(
    profile: str,
    content_root: Path | None,
    blocked: list[dict],
    reason_of=lambda r: r["email_status"] or r["conf"],
) -> None:
    if not blocked:
        return
    path = _pool_dir(profile, content_root) / ".blocked-log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).isoformat()
    with path.open("a", encoding="utf-8") as f:
        for r in blocked:
            f.write(
                json.dumps(
                    {
                        "logged_at": stamp,
                        "email": r["email"],
                        "company": r["company"],
                        "src": r["src"],
                        "reason": reason_of(r),
                    }
                )
                + "\n"
            )


#: Columns a source export cannot set, with the reason. Everything else in
#: :data:`MASTER_COLS` is importable and appears in the generated CSV map.
_ASSIGNED_COLUMNS = {
    "src": "set from the export's own filename",
    "conf_tier": "derived from email_status/conf by `classify_confidence`",
    "suppression": "re-derived from the suppression ledger on every sweep",
    "suppression_date": "re-derived from the suppression ledger on every sweep",
    "judge_verdict": "written by the judge; an input claiming to be judged is not",
    "judge_verdict_reason": "written by the judge",
    "judge_calibrated": "written by the judge, from the profile's sealed holdouts",
    "pool_row_id": "stamped once by consolidate; never supplied",
    ACCOUNT_ID_FIELD: "stamped by latest.json, joined here; never supplied",
}

#: One-line meaning per column, for the generated map. A column with no entry still
#: renders — the sync test below fails on a MISSING column, never on a missing note,
#: because a schema that silently omits a gate-critical column is the defect this
#: generator exists to prevent.
_COLUMN_NOTES = {
    "first": "given name; a blank one is held back by the merge-field gate",
    "last": "family name",
    "email": "the dedup key for the export; blank if unverified, never guessed",
    "title": "job title, as the source gave it",
    "company": "company name, cleaned of research-note artifacts",
    "company_domain": "the company's own domain, e.g. `example.com`",
    "city": "HQ city — cross-examined against `country` by the compliance gate",
    "country": "HQ country, full name",
    "segment": "`Enterprise` / `Startup`",
    "tier": "`A` / `B`",
    "score": "numeric, no denominator",
    "conf": "source-reported confidence, if any",
    "email_status": "deliverability signal (RocketReach grade, `verified`, ...)",
    "why_now": "the research note behind the opener",
    "case_study": "the matched reference customer",
    "heat": "intent heat, 0-3",
    "top_intent_score": "raw 0-100 topic-intent score behind `heat`",
    "intent_topics": "`topic:score` pairs, highest first, semicolon-joined",
    "cohort": "ICP cohort this account was scored under",
    "qualification_path": "set only when a gate was relaxed for the run",
    "signal_source_url": "primary source for the why-now — https, re-fetchable",
    "signal_observed": "ISO date the source showed it (freshness lives here, not in the clause)",
    "signal_evidence": "verbatim span of the source the clause reduces",
    "signal_subject": "the entity the fact is ABOUT; not this company means the row is wrong",
    "signal_agent_kind": "`ai` | `human` | `none` | `unclear` — whose 'agents' the clause means",
    "category_relation": "`prospect` | `competitor` | `partner` | `adjacent` | `regulator`",
    "verdict": "`send` | `re-angle` | `drop` — the RESEARCHER's, never machine-overwritten",
    "verdict_reason": "required for any non-send verdict",
    SIGNAL_COLUMN: "the hook-matrix signal this row's own why-now attests",
}


def csv_map_markdown() -> str:
    """Render the export column map from the schema the code actually reads.

    Hand-maintained, this document omitted all nine record and verdict columns that the
    enrollment gate blocks on — so a run that followed it exactly produced a list the
    gate rejected, and the doc looked complete while doing it. Generated from
    :data:`_ALIASES` and :data:`MASTER_COLS`, it cannot omit a column that exists.
    """
    lines = [
        "# Prospect — export column map",
        "",
        "<!-- GENERATED by `python -m gtm_core.prospects schema-doc`. Do not hand-edit:",
        "     re-run the command instead. Held in sync by",
        "     tests/contracts/test_hubspot_csv_map_sync.py. -->",
        "",
        "One row per **contact**, not per account: a Tier-A account with 2 enriched",
        "contacts is 2 rows. Written as `prospects-YYYYMMDD-hubspot.csv`, and folded into",
        "the pool by `python -m gtm_core.prospects consolidate`.",
        "",
        "The **CSV Column** below is the header to write. Any listed alias is accepted, so",
        "an older export keeps importing; the first spelling is the one to write today.",
        "",
        "## Columns a run writes",
        "",
        "| CSV Column | Also accepted | Meaning |",
        "|---|---|---|",
    ]
    for col in MASTER_COLS:
        if col in _ASSIGNED_COLUMNS:
            continue
        aliases = _ALIASES.get(col, (col,))
        primary, rest = aliases[0], aliases[1:]
        also = ", ".join(f"`{a}`" for a in rest) or "—"
        lines.append(f"| `{primary}` | {also} | {_COLUMN_NOTES.get(col, '')} |")

    lines += [
        "",
        "## Columns the pipeline assigns",
        "",
        "Do not write these — a value supplied here is overwritten, and for the judge",
        "columns an input that claimed to be judged would be believed by nothing.",
        "",
        "| Column | Assigned by |",
        "|---|---|",
    ]
    lines += [f"| `{col}` | {why} |" for col, why in _ASSIGNED_COLUMNS.items()]
    lines += [
        "",
        "## The record is not optional",
        "",
        "`signal_source_url`, `signal_observed`, `signal_evidence`, `signal_subject`,",
        "`signal_agent_kind`, `category_relation`, `verdict` and `verdict_reason` are what",
        "make a row checkable. Without them a renamed company, a fact about the wrong",
        "entity, and a number that drifted from its source are undetectable by",
        "construction — see `gtm_core/signal_record.py`. The enrollment gate blocks a list",
        "that lacks them.",
        "",
        "Resolve any path this file mentions with:",
        "",
        "```bash",
        "uv run python -m gtm_core.prospects paths --profile <active>",
        "```",
        "",
    ]
    return "\n".join(lines)


def _account_item_of(row: dict) -> dict:
    """The row's account, shaped as a ``latest.json`` item for key derivation."""
    return {
        "domain": (row.get("company_domain") or "").strip(),
        "company": row.get("company") or "",
        ACCOUNT_ID_FIELD: row.get(ACCOUNT_ID_FIELD) or "",
    }


def _account_key_of(row: dict) -> str:
    """The row's account identity, in ``latest.json``'s own key space."""
    return _identity_key(_account_item_of(row))


def _account_id_index(profile: str, content_root: Path | None) -> dict[str, str]:
    """Every ``latest.json`` identity key -> that account's stamped ``account_id``.

    Built from the ledger of record rather than re-derived per row, so a pooled row
    joins to the same account the dashboard shows. Absent or unreadable is an empty
    index: a first run has no accounts to join to, which is not an error.
    """
    try:
        data = load_latest(profile, content_root)
    except (OSError, ValueError):
        return {}
    index: dict[str, str] = {}
    for item in data.get("items", []):
        account_id = str(item.get(ACCOUNT_ID_FIELD) or "").strip()
        if not account_id:
            continue
        for key in _identity_keys(item):
            index.setdefault(key, account_id)
    return index


#: The account-level fields a row inherits from its account. The six provenance fields
#: plus the researcher's verdict, plus which matrix signal column the fact belongs to.
#: Deliberately EXCLUDES ``JUDGE_COLUMNS``: `verdict` is the researcher's, written once
#: during research; `judge_*` is the judge's, written by `write-verdicts` against the
#: rendered row. Two different authors at two different grains — blurring them here is
#: exactly the confusion the split was introduced to end.
_INHERITED_RECORD_COLUMNS = (*RECORD_COLUMNS, SIGNAL_COLUMN)


def _account_record_index(profile: str, content_root: Path | None) -> dict[str, dict[str, str]]:
    """``account_id`` -> the research record fields that account carries.

    The record is a fact about the *account* — a why-now signal is about the company, not
    the person — so it is written once onto the account in ``latest.json``. But the
    enrollment gate (``account_integrity --require-verdict send``) filters *rows* in
    ``ready-to-load.csv``. Without this index the join stamped ``account_id`` and stopped,
    so a fully-researched batch still failed the gate with "kept 0/N": the judgement was
    made in one file and enforced from another, with nothing carrying it across.

    Only non-empty values are indexed, so an account with a partial record contributes
    exactly the fields it actually has and never blanks a column the row already filled.
    """
    try:
        data = load_latest(profile, content_root)
    except (OSError, ValueError):
        return {}
    index: dict[str, dict[str, str]] = {}
    for item in data.get("items", []):
        account_id = str(item.get(ACCOUNT_ID_FIELD) or "").strip()
        if not account_id:
            continue
        record = {
            col: str(item.get(col) or "").strip()
            for col in _INHERITED_RECORD_COLUMNS
            if str(item.get(col) or "").strip()
        }
        if record:
            index[account_id] = record
    return index


def _disqualified_account_keys(profile: str, content_root: Path | None) -> set[str]:
    """Account keys whose lifecycle ``status`` retires them from sending.

    ``latest.json`` is the ledger of record for account lifecycle; the pooled CSVs are
    derived views of it. Reading it here is what makes an operator's (or an eval
    writeback's) disqualification actually reach a build output — before this, nothing
    in the send-list build filtered on lifecycle status at all.

    Absent or unreadable is an empty set, never an exception: a profile with no
    latest.json is a first run, and the ledger + DNC gates still apply.
    """
    retired = {"disqualified", "do-not-contact", "closed-lost"}
    try:
        data = load_latest(profile, content_root)
    except (OSError, ValueError):
        return set()
    keys: set[str] = set()
    for item in data.get("items", []):
        if str(item.get("status") or "").strip().lower() in retired:
            for key in _identity_keys(item):
                keys.add(key)
    return keys


# --- core ---------------------------------------------------------------


def consolidate(
    profile: str,
    *,
    content_root: Path | None = None,
    dnc_file: Path | None = None,
    allow_shrink: bool = False,
    require_dnc: bool = False,
    target_markets: list[str] | None = None,
    strict_market: bool = False,
    reclassify: bool = False,
    allow_downgrade: bool = False,
    rebuild_master: bool = False,
) -> dict:
    """Sweep every ``prospects-*-hubspot.csv`` export, fold net-new emails into
    the canonical ``sequences/master-list.csv``, gate by deliverability
    confidence, and write ``ready-to-load.csv`` / ``needs-verification.csv``.

    Idempotent: safe to call after every run (even a partial one) or on a
    schedule — re-running with no new exports reproduces the same output.

    ``require_dnc`` makes a missing/empty/undatable/stale suppression cache a hard
    error instead of an empty suppression set. Unattended callers must pass it: a
    successful-looking consolidate that suppressed nothing is the failure this guards.

    ``target_markets`` (default: read from the profile's PROFILE.md) is the allowed-jurisdiction
    list — rows outside it stay in the master list but never reach ``ready-to-load.csv`` or the
    hold queue, so an out-of-market lead is never something to strip at enrollment time.
    ``strict_market`` additionally drops rows with **no** country instead of counting them.

    ``reclassify`` fixes the "correction never lands" gap: by identity, a row whose email is
    already in the master list is normally skipped outright, so if a later export of the same
    contact now carries a verification signal the earlier ingestion didn't (e.g. an "Email
    Status" column added after the fact), that upgrade is silently lost forever. With
    ``reclassify=True``, an already-present row is still never duplicated, but its
    ``email_status``/``conf``/``conf_tier`` are recomputed from the newer source row and applied
    when they represent an upgrade. Downgrades are ignored unless ``allow_downgrade=True`` — a
    later export with a *weaker* signal should not evict a row that already proved deliverable.

    ``rebuild_master`` recomputes ``conf_tier`` for every row already in the master list from its
    own stored ``email_status``/``conf`` fields, with no source export needed — for when
    ``master-list.csv`` was hand-edited directly, or ``classify_confidence``'s rules changed.
    """
    master_path = _existing_master_path(profile, content_root)
    existing = _load_master(master_path)

    rebuilt_tier_changes = 0
    if rebuild_master:
        for r in existing:
            new_tier = classify_confidence(r["email_status"], r["conf"])
            if new_tier != r["conf_tier"]:
                rebuilt_tier_changes += 1
                r["conf_tier"] = new_tier

    by_email: dict[str, dict] = {}
    master_dups = 0
    for r in existing:
        if r["email"] in by_email:
            master_dups += 1
        else:
            by_email[r["email"]] = r

    dnc = _load_dnc(profile, content_root, dnc_file, require=require_dnc)
    market = (
        MarketGate(target_markets, strict=strict_market)
        if target_markets is not None
        else _resolve_market_gate(profile, strict_market)
    )
    sent_emails, sent_people = _load_sent(profile, content_root)
    existing_emails = {r["email"] for r in existing}

    net_new, dup_rows, already_present, dnc_hits, reclassified = 0, 0, 0, 0, 0
    for path in sorted(_prospects_dir(profile, content_root).glob("prospects-*-hubspot.csv")):
        with path.open(newline="", encoding="utf-8", errors="ignore") as f:
            for row in csv.DictReader(f):
                email = _get(row, "email").lower()
                if not email or "@" not in email:
                    continue
                rec = _row_to_record(row, path.name)
                # Suppression is checked against the record (not the bare address) so a
                # domain-typed DNC entry can match on company_domain too.
                if dnc.blocks(email, rec["company_domain"]):
                    dnc_hits += 1
                    continue
                if email in existing_emails:
                    already_present += 1
                    if reclassify:
                        current = by_email[email]
                        old_rank = _CONF_RANK[current["conf_tier"]]
                        new_rank = _CONF_RANK[rec["conf_tier"]]
                        if new_rank > old_rank or (
                            allow_downgrade and rec["conf_tier"] != current["conf_tier"]
                        ):
                            current["email_status"] = rec["email_status"]
                            current["conf"] = rec["conf"]
                            current["conf_tier"] = rec["conf_tier"]
                            reclassified += 1
                    continue
                if email in by_email:
                    # collision within this sweep's net-new rows
                    if _score_num(rec["score"]) > _score_num(by_email[email]["score"]):
                        by_email[email] = rec
                    dup_rows += 1
                    continue
                by_email[email] = rec
                net_new += 1

    all_rows = list(by_email.values())
    if not allow_shrink and master_path is not None and len(all_rows) < len(existing):
        raise ValueError(
            f"refusing to shrink master list: {len(existing)} -> {len(all_rows)} rows; "
            "pass allow_shrink=True to override"
        )

    # Stamp identity before anything downstream reads it. Both ids are assigned once and
    # never reassigned — a join key that changes under a row is worse than none.
    account_index = _account_id_index(profile, content_root)
    account_records = _account_record_index(profile, content_root)
    rows_stamped, accounts_joined, orphan_rows, records_joined = 0, 0, 0, 0
    for row in all_rows:
        if not str(row.get("pool_row_id") or "").strip():
            row["pool_row_id"] = f"r-{uuid.uuid4().hex[:10]}"
            rows_stamped += 1
        if not str(row.get(ACCOUNT_ID_FIELD) or "").strip():
            hit = next(
                (
                    account_index[k]
                    for k in _identity_keys(_account_item_of(row))
                    if k in account_index
                ),
                "",
            )
            if hit:
                row[ACCOUNT_ID_FIELD] = hit
                accounts_joined += 1
            else:
                orphan_rows += 1
        # Carry the account's research record down onto its rows, so the verdict is
        # readable where the gate actually filters. Fill-only: a value the row already
        # carries came from its own source export, which is the more specific artifact,
        # and is never overwritten. Re-derived from `latest.json` on every sweep, so a
        # rebuild cannot drop it and a corrected record propagates on the next pass —
        # the same reason suppression stamping was moved inside this loop.
        record = account_records.get(str(row.get(ACCOUNT_ID_FIELD) or "").strip())
        if record:
            filled = False
            for col, value in record.items():
                if not str(row.get(col) or "").strip():
                    row[col] = value
                    filled = True
            if filled:
                records_joined += 1

    # Re-derive the suppression cache from the LEDGER on every sweep, before the master
    # is written. The two columns are in MASTER_COLS now, so they survive the projection;
    # stamping here is what makes them true. Previously `suppression apply` was a separate
    # command a human had to remember to re-run after each consolidate, and the one time
    # nobody did, 55 exclusions were silently wiped an hour after being recorded.
    ledger_index = load_suppression_index(prospect_paths.suppression_ledger(profile, content_root))
    ledger_marked = 0
    for row in all_rows:
        hit = ledger_index.match(row)
        if hit:
            row["suppression"] = hit.reason
            row["suppression_date"] = hit.date
            ledger_marked += 1

    pool = _pool_dir(profile, content_root)
    master_canonical = pool / "master-list.csv"
    _snapshot(master_canonical, pool / ".snapshots")
    _atomic_write_csv(master_canonical, all_rows)

    blocked = [r for r in all_rows if r["conf_tier"] == "blocked"]
    _append_blocked_log(profile, content_root, blocked)

    # Accounts an operator or an eval writeback has retired. Until 2026-08-27 the build
    # read no lifecycle status at all, so `status: disqualified` was decoration: the row
    # came back in the very next ready-to-load.csv.
    disqualified_keys = _disqualified_account_keys(profile, content_root)
    suppressed_excluded, disqualified_excluded = 0, 0
    ready_blocked: list[dict] = []

    # Loadable = not DNC'd, not already-sent (by email), not blocked-confidence, not
    # locally suppressed, not on a disqualified account. Re-checked against the full
    # master (not just this sweep's net-new rows) so a row folded in before an
    # address/domain was suppressed is dropped on the next sweep.
    loadable = []
    for r in all_rows:
        if dnc.blocks(r["email"], r["company_domain"]) or r["email"] in sent_emails:
            continue
        if r["conf_tier"] == "blocked":
            continue
        if (r.get("suppression") or "").strip():
            suppressed_excluded += 1
            ready_blocked.append({**r, "blocked_reason": f"suppressed:{r['suppression']}"})
            continue
        if _account_key_of(r) in disqualified_keys:
            disqualified_excluded += 1
            ready_blocked.append({**r, "blocked_reason": "status-disqualified"})
            continue
        loadable.append(r)

    if ready_blocked:
        _append_blocked_log(profile, content_root, ready_blocked)

    # Jurisdiction pass: a lead outside target_markets is dropped from BOTH the ready list and the
    # hold queue — verifying it would push their PII to the sequencer for a send that can't happen.
    # The row stays in the master list; this is a load gate, not a delete.
    out_of_market_excluded, unknown_country = 0, 0
    if market:
        in_market: list[dict] = []
        for r in loadable:
            if not (r.get("country") or "").strip():
                unknown_country += 1
            if market.blocks(r.get("country", "")):
                out_of_market_excluded += 1
                continue
            in_market.append(r)
        loadable = in_market

    # Person-level pass: drop anyone already sent under a DIFFERENT address, and
    # collapse the same human carrying two email formats to their best row.
    sent_person_excluded = 0
    best_by_person: dict[str, dict] = {}
    person_unique: list[dict] = []
    person_dups_collapsed = 0
    for r in loadable:
        pk = _person_key(r)
        if pk and pk in sent_people:
            sent_person_excluded += 1
            continue
        if not pk:
            person_unique.append(r)  # can't identify a person — never collapse
            continue
        prev = best_by_person.get(pk)
        if prev is None:
            best_by_person[pk] = r
            person_unique.append(r)
            continue
        person_dups_collapsed += 1
        # keep the higher-confidence row, breaking ties on score
        if (_CONF_RANK[r["conf_tier"]], _score_num(r["score"])) > (
            _CONF_RANK[prev["conf_tier"]],
            _score_num(prev["score"]),
        ):
            person_unique[person_unique.index(prev)] = r
            best_by_person[pk] = r

    # Merge-field gate: a row whose {{First Name}}/{{Company}} would render a broken or
    # obviously-templated email never reaches the load file, even at high confidence. The
    # repair in _row_to_record already fixed everything mechanically fixable, so what is
    # left needs a human — guessing someone's name is not a repair this code will make up.
    # Excluded, logged, and counted; never silently dropped.
    merge_blocked = [r for r in person_unique if r["conf_tier"] == "high" and mh_blocks(r)]
    if merge_blocked:
        _append_blocked_log(
            profile,
            content_root,
            merge_blocked,
            reason_of=lambda r: (
                "merge-field: " + ", ".join(f.rule for f in mh_check_row(r) if f.level == "block")
            ),
        )
    merge_blocked_emails = {r["email"] for r in merge_blocked}

    ready = [
        r
        for r in person_unique
        if r["conf_tier"] == "high" and r["email"] not in merge_blocked_emails
    ]
    needs_verification = [r for r in person_unique if r["conf_tier"] in ("medium", "unknown")]

    _atomic_write_csv(ready_to_load_path(profile, content_root), ready)
    _atomic_write_csv(needs_verification_path(profile, content_root), needs_verification)

    result = {
        "profile": profile,
        "master_total": len(all_rows),
        "master_dups_collapsed": master_dups,
        "net_new_folded": net_new,
        "net_new_dups_collapsed": dup_rows,
        "already_in_master_skipped": already_present,
        "reclassified_from_source": reclassified,
        "rebuilt_master_tier_changes": rebuilt_tier_changes,
        "dnc_hits_blocked": dnc_hits,
        "pool_row_ids_stamped": rows_stamped,
        "accounts_joined": accounts_joined,
        "records_joined": records_joined,
        "orphan_rows": orphan_rows,
        "ledger_suppressions_marked": ledger_marked,
        "suppressed_excluded": suppressed_excluded,
        "disqualified_excluded": disqualified_excluded,
        "market_gate": ", ".join(market.markets) if market else "off",
        "out_of_market_excluded": out_of_market_excluded,
        "unknown_country": unknown_country,
        "sent_person_excluded": sent_person_excluded,
        "person_dups_collapsed": person_dups_collapsed,
        "ready_to_load": len(ready),
        "needs_verification": len(needs_verification),
        "merge_field_excluded": len(merge_blocked),
        "blocked_excluded": len(blocked),
        "master_list": str(master_canonical),
        "ready_to_load_path": str(ready_to_load_path(profile, content_root)),
        "needs_verification_path": str(needs_verification_path(profile, content_root)),
    }
    _print_banner(result)
    # Refresh the operator-facing status page on every sweep so it can never go
    # stale. Best-effort: a dashboard error must never break consolidation.
    # One page, one refresh. Two pages fed by one hand-dropped snapshot is how they
    # drifted apart; the retired URLs are rewritten as redirects so links survive.
    try:
        from gtm_core.email_campaign_dashboard import render_dashboard as _render_gtm

        _render_gtm(profile, content_root)
    except Exception as exc:  # noqa: BLE001 - dashboard is non-critical
        print(f"dashboard refresh skipped: {exc}", file=sys.stderr)
    return result


def split_by_signal(profile: str, content_root: Path | None = None) -> dict:
    """Split ``ready-to-load.csv`` into a signal-led list and a generic one.

    A merge sequence can only open on a "why now" that EVERY enrolled row carries — an
    empty merge tag renders a broken sentence, so one list cannot serve both populations.
    This writes two, each matched to the copy it can support:

    * ``ready-to-load-signal.csv``  — rows whose ``why_now`` reduces to a clause safe to
      render (:func:`gtm_core.merge_hygiene.signal_clause`), with that clause in a
      ``signal_clause`` column for the sequencer to map to a custom field.
    * ``ready-to-load-generic.csv`` — everything else, for the existing generic copy.

    Fail-closed: a row whose signal cannot be reduced verbatim and safely lands in the
    generic list. Intent-topic scores ("machine learning (intent score 81)") and research
    notes recording the ABSENCE of a signal are never treated as signals.

    A clause must clear TWO independent gates, because being well-formed is not the same
    as being true today. :func:`~gtm_core.merge_hygiene.signal_clause` judges the shape;
    :func:`~gtm_core.merge_hygiene.signal_is_fresh` judges the age, since the copy opens
    on "saw the news" and a 15-month-old acquisition is not news. A stale or undated
    clause is demoted to the generic arc, which makes no recency claim.
    """
    ready = _load_master(ready_to_load_path(profile, content_root))
    signal_rows: list[dict] = []
    generic_rows: list[dict] = []
    stale_signal = 0
    for row in ready:
        clause = signal_clause(row.get("why_now", ""))
        if clause and signal_is_fresh(clause):
            signal_rows.append({**row, "signal_clause": clause})
        else:
            if clause:
                stale_signal += 1
            generic_rows.append(row)

    seq_dir = _sequences_dir(profile, content_root)
    signal_path = seq_dir / "ready-to-load-signal.csv"
    generic_path = seq_dir / "ready-to-load-generic.csv"

    _atomic_write_csv_cols(signal_path, signal_rows, [*MASTER_COLS, "signal_clause"])
    _atomic_write_csv_cols(generic_path, generic_rows, MASTER_COLS)

    populated = sum(1 for r in ready if (r.get("why_now") or "").strip())
    result = {
        "profile": profile,
        "ready_total": len(ready),
        "why_now_populated": populated,
        "signal_led": len(signal_rows),
        "generic": len(generic_rows),
        "why_now_unusable": populated - len(signal_rows),
        "signal_stale_demoted": stale_signal,
        "signal_path": str(signal_path),
        "generic_path": str(generic_path),
    }
    print(
        f"split[{profile}]: {result['signal_led']} signal-led · {result['generic']} generic "
        f"({result['why_now_unusable']} of {populated} why_now values were not usable "
        f"as an opening clause)",
        file=sys.stderr,
    )
    return result


def _print_banner(result: dict) -> None:
    """One human-readable status line to stderr (stdout stays clean JSON) — the
    'one number, always shown' surface so readiness never needs archaeology."""
    print(
        f"pool[{result['profile']}]: {result['ready_to_load']} ready · "
        f"{result['needs_verification']} verifying · "
        f"{result['blocked_excluded']} blocked · "
        f"{result['net_new_folded']} net-new folded"
        + (
            f" · {result['merge_field_excluded']} merge-field blocked"
            if result.get("merge_field_excluded")
            else ""
        )
        + (
            f" · {result['sent_person_excluded']} already-contacted skipped"
            if result.get("sent_person_excluded")
            else ""
        )
        + (
            f" · {result['out_of_market_excluded']} out-of-market"
            if result.get("out_of_market_excluded")
            else ""
        )
        + (
            f" · {result['unknown_country']} unknown-country"
            if result.get("unknown_country")
            else ""
        )
        + (
            f" · {result['reclassified_from_source']} reclassified"
            if result.get("reclassified_from_source")
            else ""
        )
        + (
            f" · {result['rebuilt_master_tier_changes']} rebuilt-tiers"
            if result.get("rebuilt_master_tier_changes")
            else ""
        ),
        file=sys.stderr,
    )


def pool_status(profile: str, content_root: Path | None = None) -> dict:
    """Cheap read-only summary of the last consolidation run — no MCP, no
    re-sweep. Safe for a cockpit brief or a daily heartbeat check."""
    master = _load_master(_pool_dir(profile, content_root) / "master-list.csv")
    unconsolidated = 0
    master_emails = {r["email"] for r in master}
    for path in _prospects_dir(profile, content_root).glob("prospects-*-hubspot.csv"):
        with path.open(newline="", encoding="utf-8", errors="ignore") as f:
            for row in csv.DictReader(f):
                email = _get(row, "email").lower()
                if email and "@" in email and email not in master_emails:
                    unconsolidated += 1
    by_tier = {"high": 0, "medium": 0, "unknown": 0, "blocked": 0}
    for r in master:
        by_tier[r["conf_tier"]] = by_tier.get(r["conf_tier"], 0) + 1
    return {
        "master_total": len(master),
        "by_confidence_tier": by_tier,
        "unconsolidated_in_raw_exports": unconsolidated,
    }


# Per-variant glob patterns — public, and the ONE place each dossier variant's file
# naming is defined. gtm_core.account_integrity.dossier_depth() imports these three
# directly rather than keeping its own copy: a second hand-maintained list is exactly
# how the "research pack" variant below went unrecognized for a day after it shipped.
DOSSIER_GLOB_FULL = ("account-dossier-*",)
DOSSIER_GLOB_ONEPAGER = ("*-onepager-*",)
DOSSIER_GLOB_BRIEF = (
    "prospecting-brief-*",
    # the "research pack, no visuals" variant (see account-dossier/SKILL.md §"Research
    # pack variant") — a markdown-only dossier used for bulk coverage where a
    # docx+PDF+images pass per account isn't affordable. Named `dossier-<slug>-<date>.md`
    # to stay visually distinct from the docx `account-dossier-*` full variant. Added
    # 2026-08-13 after this pattern list caused a false `no-dossier` reading on all 439
    # accounts dossiered this way on 2026-08-12 — the files existed, this glob just
    # never looked for them.
    "dossier-*.md",
)
_DOSSIER_GLOB_PATTERNS = DOSSIER_GLOB_FULL + DOSSIER_GLOB_ONEPAGER + DOSSIER_GLOB_BRIEF


def _accounts_dir(profile: str, content_root: Path | None = None) -> Path:
    root = content_root or resolve_content_root()
    return root / _safe_segment(profile, "profile") / "accounts"


def _folder_has_dossier(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    return any(any(folder.glob(pat)) for pat in _DOSSIER_GLOB_PATTERNS)


# Geographic qualifiers only — a suffix naming WHERE a company operates, never WHICH
# part of it. "vertex-chartered-singapore" is the same account as "Vertex Chartered";
# a "…-ventures"/"…-capital" arm is a separate entity with its own buyers and its own
# research. Adding a business-unit word here would let the ``no-dossier`` gate accept a
# subsidiary's dossier in place of the parent's — the failure mode this list exists to
# avoid. Keep it geographic.
_GEO_QUALIFIERS = frozenset(
    {
        "apac",
        "asia",
        "americas",
        "anz",
        "australia",
        "brazil",
        "canada",
        "china",
        "emea",
        "europe",
        "france",
        "germany",
        "global",
        "hong kong",
        "india",
        "indonesia",
        "international",
        "italy",
        "japan",
        "korea",
        "malaysia",
        "mena",
        "mexico",
        "middle east",
        "netherlands",
        "new zealand",
        "nordics",
        "philippines",
        "saudi arabia",
        "sea",
        "singapore",
        "south africa",
        "south korea",
        "spain",
        "sweden",
        "switzerland",
        "taiwan",
        "thailand",
        "uae",
        "uk",
        "us",
        "usa",
        "united kingdom",
        "united states",
        "vietnam",
    }
)


def _drop_geo_suffix(name: str) -> str:
    """``vertex-chartered-singapore`` -> ``vertex chartered``; a name with no known
    geographic suffix comes back unchanged.

    Splits on word boundaries *before* tokenising, deliberately: matching the suffix
    against the collapsed org token would strip a name that merely *ends in* those
    letters ("Nexus" -> "Nex" via ``us``, "Undersea" -> "Under" via ``sea``). Only one
    qualifier is dropped, and never the whole name.
    """
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", name) if p]
    for width in (2, 1):  # two-word qualifiers first ("hong kong", "united kingdom")
        if len(parts) > width and " ".join(parts[-width:]).lower() in _GEO_QUALIFIERS:
            return " ".join(parts[:-width])
    return name


def account_has_dossier(
    profile: str, company: str, company_domain: str = "", content_root: Path | None = None
) -> tuple[bool, str]:
    """True if this account already has a dossier of any kind, checked two ways: the
    canonical slug path, and a fuzzy match against every existing account folder name
    (via the same org-token identity used elsewhere in this pipeline for account dedup)
    — so an account hand-dossiered under a differently-spelled legacy folder (before the
    canonical slug existed) is never silently re-generated under a second, duplicate
    folder. The canonical slug is not retrofitted onto old folders; this check is what
    lets a new-vs-legacy folder name still resolve to the same account.

    The fuzzy pass compares a folder's token against **both** of the account's tokens —
    the domain-derived one and the name-derived one. Comparing only the domain-derived
    token (the shape this had until 2026-08-25) made the whole fallback inert for every
    account whose registered domain *abbreviates* its name, a very common shape for
    banks, airlines and industrials: ``org_token("vc.example", "Vertex Chartered")`` is
    ``vc`` and can never equal ``vertexchartered``, so for that entire class the promise
    in the paragraph above silently did not hold, and the sweep re-generated a second
    folder every time. A single trailing geographic qualifier is tolerated on one side
    (``vertex-chartered-singapore`` matches ``Vertex Chartered``) but never on both, so
    two genuinely distinct regional accounts still read as distinct.

    Exact token matches win over qualifier-tolerant ones, and folders are scanned in
    sorted order, so ``matched_folder_name`` is deterministic when several folders
    could match.

    Public — also the dossier-existence primitive :mod:`gtm_core.account_integrity`
    reuses for its ``no-dossier`` gate; keep this the one place that logic lives.
    Loosening it further trades a duplicate folder for a masked missing-research
    finding, which is the more expensive of the two errors.

    Returns ``(has_dossier, matched_folder_name)`` — ``matched_folder_name`` is ``""``
    when no dossier was found.
    """
    accounts_dir = _accounts_dir(profile, content_root)
    canonical = slug(company)
    if canonical and _folder_has_dossier(accounts_dir / canonical):
        return True, canonical

    domain_token = org_token(company_domain, "")
    name_token = org_token("", company)
    # The account's own name minus a geographic qualifier, for the mirror case where
    # the *company* carries the qualifier and the folder does not.
    company_base = org_token("", _drop_geo_suffix(company)) if name_token else ""
    if not (domain_token or name_token) or not accounts_dir.is_dir():
        return False, ""

    candidates = [f for f in sorted(accounts_dir.iterdir()) if f.name != canonical]
    exact = {t for t in (domain_token, name_token) if t}
    for folder in candidates:
        if org_token("", folder.name) in exact and _folder_has_dossier(folder):
            return True, folder.name
    if not name_token:
        return False, ""
    for folder in candidates:
        folder_token = org_token("", folder.name)
        folder_base = org_token("", _drop_geo_suffix(folder.name))
        # One side qualified, the other bare. Never base-vs-base: that would collapse
        # a "…-singapore" account into a "…-malaysia" one.
        if folder_base == name_token or (company_base and folder_token == company_base):
            if _folder_has_dossier(folder):
                return True, folder.name
    return False, ""


def accounts_needing_dossier(
    profile: str, content_root: Path | None = None, *, tier: str | None = "A"
) -> list[dict]:
    """Accounts in ``master-list.csv`` with no dossier yet — the candidate list the
    ``prospect`` skill's dossier sweep loops over to auto-generate a prospecting brief +
    outreach draft for each. One entry per account (deduped by the same ``org_token``
    identity used throughout this module), not per person.

    ``tier="A"`` (default) preserves the original Tier-A-only sweep. Pass ``tier=None``
    to check every account regardless of tier — the wider check the sweep now runs
    before a bulk sequence load, since a Tier-B/bulk-sourced account reaching a merge
    sequence with zero research behind its Why Now clause is exactly the gap that let
    439 of 496 contacts ship with no dossier on 2026-08-12.

    Idempotent by construction: re-running this after the sweep generates a dossier
    for an account removes that account from the list on its own — no separate state
    file needed, the filesystem is the source of truth (see ``account_has_dossier``).
    """
    master = _load_master(_pool_dir(profile, content_root) / "master-list.csv")
    seen_tokens: set[str] = set()
    out: list[dict] = []
    for r in master:
        if tier is not None and (r.get("tier") or "").strip().upper() != tier.upper():
            continue
        token = org_token(r.get("company_domain", ""), r.get("company", ""))
        if not token or token in seen_tokens:
            continue
        seen_tokens.add(token)
        has_dossier, matched = account_has_dossier(
            profile, r["company"], r.get("company_domain", ""), content_root
        )
        if has_dossier:
            continue
        out.append(
            {
                "company": r["company"],
                "company_domain": r.get("company_domain", ""),
                "canonical_slug": slug(r["company"]),
                "existing_legacy_folder": matched or None,
                "why_now": r.get("why_now", ""),
                "cohort": r.get("cohort", ""),
                "top_intent_score": r.get("top_intent_score", ""),
            }
        )
    return out


def tier_a_needing_dossier(profile: str, content_root: Path | None = None) -> list[dict]:
    """Back-compat wrapper — Tier-A only. See :func:`accounts_needing_dossier`."""
    return accounts_needing_dossier(profile, content_root, tier="A")


def next_verification_batch(
    profile: str,
    limit: int = 50,
    content_root: Path | None = None,
    *,
    dnc_file: Path | None = None,
    require_dnc: bool = False,
) -> list[dict]:
    """The top ``limit`` rows from the hold queue, highest score first, shaped as
    Saleshandy prospect objects — the single call the email-sequence skill makes
    to auto-drain ``needs-verification.csv`` (import these with verify=true, let
    the sequencer's verifier grade them, survivors re-gate to ready on the next
    sweep). Rows without a usable first/last/email are skipped.

    Suppression is re-checked here even though ``consolidate`` already filtered the
    hold queue: the file on disk is a snapshot, and someone can opt out between the
    sweep that wrote it and the import that drains it. The provider only enforces its
    own DNC at *send* time, so this is the last point at which we can avoid handing a
    suppressed address to a third party at all.
    """
    dnc = _load_dnc(profile, content_root, dnc_file, require=require_dnc)
    rows = _load_master(needs_verification_path(profile, content_root))
    rows.sort(key=lambda r: _score_num(r["score"]), reverse=True)
    out: list[dict] = []
    for r in rows:
        if dnc.blocks(r["email"], r["company_domain"]):
            continue
        if not (r["first"] and r["last"] and r["email"]):
            continue
        out.append(
            {
                "First Name": r["first"],
                "Last Name": r["last"],
                "Email": r["email"],
                "Company": r["company"],
                "Job Title": r["title"],
                # Saleshandy's actual field label is "Company Domain" (confirmed via
                # list_fields 2026-07-23) — NOT "Company Domain Name", which the
                # import API rejects with a 400.
                "Company Domain": r["company_domain"],
                "Country": r["country"],
            }
        )
        if len(out) >= limit:
            break
    return out


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gtm_core.prospects_consolidate")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("consolidate", help="sweep raw exports into the master list")
    c.add_argument("--profile", required=True)
    c.add_argument(
        "--dnc-file", help="path to a JSON list of DNC emails (default: .cache/dnc-emails.json)"
    )
    c.add_argument("--allow-shrink", action="store_true")
    c.add_argument(
        "--require-dnc",
        action="store_true",
        help="fail hard if the DNC cache is missing/empty/undatable/stale instead of "
        "consolidating with no suppression (use for every unattended run)",
    )
    c.add_argument(
        "--market",
        action="append",
        help="override the profile's target_markets (repeatable); rows outside it never "
        "reach ready-to-load.csv",
    )
    c.add_argument(
        "--strict-market",
        action="store_true",
        help="also drop rows with no country — unresolved is not the same as allowed",
    )
    c.add_argument(
        "--reclassify",
        action="store_true",
        help="re-run classify_confidence() against the newer source row for an already-"
        "present email, upgrading conf_tier when the fresh export carries a better "
        "verification signal (e.g. a late-added Email Status column)",
    )
    c.add_argument(
        "--allow-downgrade",
        action="store_true",
        help="with --reclassify, also apply a WEAKER conf_tier from the newer source row "
        "(default: upgrades only)",
    )
    c.add_argument(
        "--rebuild-master",
        action="store_true",
        help="recompute conf_tier for every row already in master-list.csv from its own "
        "stored email_status/conf fields — no new source export needed",
    )

    s = sub.add_parser("status", help="read-only pool status, no re-sweep")
    s.add_argument("--profile", required=True)

    v = sub.add_parser("verify-batch", help="emit next N hold-queue rows as Saleshandy import JSON")
    v.add_argument("--profile", required=True)
    v.add_argument("--limit", type=int, default=50)
    v.add_argument("--dnc-file", help="path to the DNC cache (default: .cache/dnc-emails.json)")
    v.add_argument(
        "--require-dnc",
        action="store_true",
        help="fail hard rather than emitting a batch checked against no suppression list",
    )

    sp = sub.add_parser(
        "split-by-signal",
        help="split ready-to-load.csv into a signal-led list and a generic one",
    )
    sp.add_argument("--profile", required=True)

    ta = sub.add_parser(
        "tier-a-needing-dossier",
        help="Tier-A accounts with no dossier yet -> candidates for the prospecting-brief sweep",
    )
    ta.add_argument("--profile", required=True)

    an = sub.add_parser(
        "accounts-needing-dossier",
        help="accounts with no dossier yet, any tier by default -> candidates for the "
        "dossier sweep before a bulk sequence load",
    )
    an.add_argument("--profile", required=True)
    an.add_argument(
        "--tier",
        default="",
        help="restrict to one tier (e.g. A); default is every tier",
    )

    args = ap.parse_args(argv)
    if args.cmd == "consolidate":
        result = consolidate(
            args.profile,
            dnc_file=Path(args.dnc_file) if args.dnc_file else None,
            allow_shrink=args.allow_shrink,
            require_dnc=args.require_dnc,
            target_markets=args.market,
            strict_market=args.strict_market,
            reclassify=args.reclassify,
            allow_downgrade=args.allow_downgrade,
            rebuild_master=args.rebuild_master,
        )
    elif args.cmd == "verify-batch":
        result = next_verification_batch(
            args.profile,
            limit=args.limit,
            dnc_file=Path(args.dnc_file) if args.dnc_file else None,
            require_dnc=args.require_dnc,
        )
    elif args.cmd == "split-by-signal":
        result = split_by_signal(args.profile)
    elif args.cmd == "tier-a-needing-dossier":
        result = tier_a_needing_dossier(args.profile)
    elif args.cmd == "accounts-needing-dossier":
        result = accounts_needing_dossier(args.profile, tier=args.tier or None)
    else:
        result = pool_status(args.profile)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
