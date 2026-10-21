from __future__ import annotations

import re

from .company import (
    _COMPANY_NON_NAMES,
    _LEGAL_SUFFIX_RE,
    _TRADEMARK_RE,
    _TRANSACTION_ENTITY_RE,
    QUALIFICATION_SCORE_MAX,
    SEGMENTS,
    clean_company,
    clean_segment,
)
from .email import _EMAIL_RE, _FREEMAIL, _JUNK_DOMAIN_TOKENS, _ROLE_LOCAL_RE, bare_host
from .model import Finding
from .names import (
    _CREDENTIAL_RE,
    _NAME_CHARS_RE,
    _NON_NAMES,
    _strip_symbols,
    clean_first_name,
    clean_last_name,
)
from .titles import _TITLE_BIO_RE, clean_title


def check_row(row: dict) -> list[Finding]:
    """Judge one prospect row's merge fields **after** :func:`clean_row` has run.

    ``block`` findings mean the row cannot be sent as-is; ``warn`` findings are
    advisory. Reads ``first``, ``last``, ``email``, ``company``, ``company_domain``.
    """
    out: list[Finding] = []
    first = (row.get("first") or "").strip()
    company = (row.get("company") or "").strip()
    email = (row.get("email") or "").strip()
    # Compare on the HOST, never the raw field. Two normalisation bugs have invented a
    # domain mismatch here: lstrip("www.") stripped a CHARACTER SET and turned
    # "wavelet.example" into "avelet.example"; and a bare removeprefix left a provider's
    # landing-page path in place, so "acme.example/sg" never equalled "acme.example". The
    # raw value
    # is kept for the junk check below — normalising must not hide the defect.
    raw_domain = (row.get("company_domain") or "").strip().lower()
    domain = bare_host(raw_domain)

    # -- first name: drives the greeting, so a defect here is always visible.
    if not first:
        out.append(Finding("block", "first", "first-name-empty", "no usable given name"))
    elif first.lower() in _NON_NAMES:
        out.append(Finding("block", "first", "first-name-placeholder", repr(first)))
    elif not _NAME_CHARS_RE.match(first):
        out.append(Finding("block", "first", "first-name-unrenderable", f"{first!r} is not a name"))
    elif len(first) == 1:
        out.append(Finding("warn", "first", "first-name-initial", repr(first)))

    # -- company: appears mid-sentence and possessivized, 2-3x per touch.
    if not company:
        out.append(Finding("block", "company", "company-empty", "no company name"))
    elif company.lower() in _COMPANY_NON_NAMES:
        out.append(Finding("block", "company", "company-placeholder", repr(company)))
    else:
        if re.search(r"[|•·]", company):
            out.append(Finding("block", "company", "company-headline", repr(company)))
        if _TRADEMARK_RE.search(company):
            out.append(Finding("block", "company", "company-trademark-glyph", repr(company)))
        if re.search(r"[!?]", company):
            out.append(Finding("block", "company", "company-sentence-punct", repr(company)))
        if company.endswith("."):
            out.append(Finding("block", "company", "company-trailing-period", repr(company)))
        if _TRANSACTION_ENTITY_RE.search(company):
            out.append(Finding("block", "company", "company-transaction-entity", repr(company)))
        if _LEGAL_SUFFIX_RE.search(company):
            out.append(Finding("warn", "company", "company-legal-suffix", repr(company)))
        if "(" in company or ")" in company:
            out.append(Finding("warn", "company", "company-parenthetical", repr(company)))
        if len(company) > 32:
            out.append(
                Finding("warn", "company", "company-long", f"{len(company)} chars: {company!r}")
            )
        if company.isupper() and len(company) > 5:
            out.append(Finding("warn", "company", "company-allcaps", repr(company)))

    # -- email: the send target itself.
    if not _EMAIL_RE.match(email):
        out.append(Finding("block", "email", "email-malformed", repr(email)))
    else:
        local, _, edom = email.partition("@")
        edom = edom.lower()
        if _ROLE_LOCAL_RE.match(local):
            out.append(Finding("block", "email", "email-role-address", email))
        if edom in _FREEMAIL:
            out.append(Finding("warn", "email", "email-freemail", edom))
        if (
            domain
            and edom != domain
            and not (edom.endswith("." + domain) or domain.endswith("." + edom))
        ):
            out.append(Finding("warn", "email", "email-domain-mismatch", f"{edom} != {domain}"))
    if any(tok in raw_domain for tok in _JUNK_DOMAIN_TOKENS):
        out.append(Finding("warn", "company", "company-domain-junk", repr(raw_domain)))

    # -- last name / title: not in today's copy, so advisory here. The merge-render
    # linter escalates these to errors when a template actually uses {{Last Name}} or
    # {{Job Title}} — severity should follow what the copy renders, not what it might.
    last = (row.get("last") or "").strip()
    if last:
        if _strip_symbols(last) != last:
            out.append(Finding("warn", "last", "last-name-symbols", repr(last)))
        if _CREDENTIAL_RE.search(last):
            out.append(Finding("warn", "last", "last-name-credentials", repr(last)))
        # A single letter is an initial, never a surname. Length alone is NOT the signal:
        # Yu, Ma, Ng, Li, Oh and Ha are real surnames, and flagging them would teach the
        # operator to ignore this rule — which costs more than the rule earns.
        if len(_strip_symbols(last)) == 1:
            out.append(Finding("warn", "last", "last-name-initial", repr(last)))

    title = (row.get("title") or "").strip()
    if title:
        if re.search(r"[|•·]", title):
            out.append(Finding("warn", "title", "title-headline", repr(title)))
        if _TITLE_BIO_RE.search(title):
            out.append(Finding("warn", "title", "title-bio-fragment", repr(title)))

    # Segment is only checked when the row carries the column at all — most merge CSVs
    # do not, and a missing optional column is not a defect.
    if "segment" in row:
        segment = (row.get("segment") or "").strip()
        if segment and segment != clean_segment(segment):
            out.append(
                Finding(
                    "warn",
                    "segment",
                    "segment-noncanonical",
                    f"{segment!r} != {clean_segment(segment)!r}",
                )
            )
        elif segment and segment not in SEGMENTS:
            out.append(Finding("warn", "segment", "segment-unknown", repr(segment)))

    out.extend(_check_score(row))
    return out


def _check_score(row: dict) -> list[Finding]:
    """Judge ``row["score"]`` against the plausible range of a qualification verdict.

    Split out of :func:`check_row` (2026-09-04) to keep that function under the statement
    ratchet — not a sign this belongs elsewhere; it is the same "only judged when the row
    carries the column" arrangement as segment, kept in its own function for size alone.

    A value above the plausible rubric ceiling is not a high-scoring account — it is a
    number from a different scorer on a different scale, which is how a 0-53 spend ranking
    reached 749 published rows in the qualification column. `warn`, not `block`: the row
    itself may be perfectly sendable, and what is wrong is the provenance of one field.
    """
    if "score" not in row:
        return []
    raw_score = str(row.get("score") or "").strip()
    if not raw_score:
        return []
    try:
        value = int(float(raw_score))
    except ValueError:
        return [Finding("warn", "score", "score-not-numeric", repr(raw_score))]
    if value > QUALIFICATION_SCORE_MAX or value < 0:
        return [
            Finding(
                "warn",
                "score",
                "score-out-of-range",
                f"{value} outside 0-{QUALIFICATION_SCORE_MAX} "
                "(a qualification verdict, not a spend ranking)",
            )
        ]
    return []


def clean_row(row: dict) -> dict:
    """Return a shallow copy of ``row`` with every merge field repaired.

    Idempotent: cleaning an already-clean row is a no-op, so it is safe to run on every
    consolidation sweep. ``first`` is derived BEFORE ``last`` is cleaned, because the
    swapped-field recovery reads the raw ``last`` column.
    """
    out = dict(row)
    cleaned = {
        "first": clean_first_name(row.get("first", ""), row.get("last", ""), row.get("email", "")),
        "last": clean_last_name(row.get("last", "")),
        "company": clean_company(row.get("company", "")),
        "title": clean_title(row.get("title", "")),
        "segment": clean_segment(row.get("segment", "")),
    }
    # Only write back fields the caller actually carries — inventing a key would change
    # the row's shape and surprise anything comparing rows or writing a fixed CSV schema.
    out.update({k: val for k, val in cleaned.items() if k in row})
    return out


def blocks(row: dict) -> bool:
    """True when ``row`` carries at least one ``block`` finding — i.e. must not load."""
    return any(f.level == "block" for f in check_row(row))
