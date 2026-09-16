from __future__ import annotations

import re

from ..merge_hygiene import bare_host, clean_company, clean_first_name, clean_last_name, clean_title
from ..prospects_state import _norm as _norm_company
from ..signal_record import RECORD_COLUMNS, SIGNAL_COLUMN
from .columns import column_value

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

# A generic inbox (hello@, enquiries@, info@) pulled directly off the company's own
# official site — not RocketReach/Apollo-graded, because there is no person to grade.
# Distinct from `_HIGH_STATUS`'s "account-folder-verified" (a human-confirmed research
# fact): this one is a specific claim ("their own domain published this address") that
# `classify_confidence` itself checks, not just records.
_SITE_PUBLISHED = re.compile(r"site-published", re.I)

# Segments where a generic inbox plausibly still reaches a decision-maker. An enterprise
# "info@" reaches a mailroom or a support queue, not a buyer — that's a different failure
# mode this status is not meant to license.
_SITE_PUBLISHED_SEGMENTS = {"builder", "startup"}


def _get(row: dict, field: str) -> str:
    """Local alias for :func:`columns.column_value` — kept because this module calls it a lot."""
    return column_value(row, field)


def _score_num(s: str) -> int:
    """``"7/12"`` -> ``7``; ``"11"`` -> ``11``; unparseable -> ``-1``."""
    s = (s or "").split("/")[0].strip()
    try:
        return int(float(s))
    except ValueError:
        return -1


def _same_domain(email: str, company_domain: str) -> bool:
    """True when ``email``'s domain is the company's own domain (or a subdomain of
    it, either direction) — the actual claim ``site-published`` makes. A row where
    the two disagree did not resolve a company-published address; it resolved
    someone else's, so the claim is false on its face regardless of segment.
    """
    email = (email or "").strip().lower()
    if "@" not in email:
        return False
    email_domain = email.rsplit("@", 1)[-1]
    company = bare_host(company_domain).lower()
    if not email_domain or not company:
        return False
    return (
        email_domain == company
        or email_domain.endswith("." + company)
        or company.endswith("." + email_domain)
    )


def classify_confidence(
    email_status: str,
    conf: str,
    segment: str = "",
    email: str = "",
    company_domain: str = "",
) -> str:
    """Deliverability confidence tier from whatever verification signal a row
    carries. No signal at all (the common case for a fresh raw export) is
    ``"unknown"`` — treated as NOT safe to load by default, not as a free pass.

    ``segment``/``email``/``company_domain`` only matter for the
    ``"site-published"`` status (see ``_SITE_PUBLISHED`` above) — every other
    status ignores them, so existing two-argument callers are unaffected.
    """
    status = (email_status or "").strip()
    c = (conf or "").strip().lower()
    if _BLOCKED_STATUS.search(status) or c == "invalid":
        return "blocked"
    if _SITE_PUBLISHED.search(status):
        if not _same_domain(email, company_domain):
            # the address didn't actually come from the company's own domain —
            # the claim this status makes is false, fail closed regardless of segment
            return "unknown"
        if (segment or "").strip().lower() in _SITE_PUBLISHED_SEGMENTS:
            return "high"
        return "medium"
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
        "conf_tier": classify_confidence(
            email_status,
            conf,
            segment=_get(row, "segment"),
            email=email,
            company_domain=_get(row, "company_domain"),
        ),
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
