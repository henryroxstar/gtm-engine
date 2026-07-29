"""Merge-field hygiene for mail-merge outreach — the gate the copy linter can't see.

The outreach copy linter (``tests/linter/outreach_pack_linter.py``) reads a *rendered*
email and judges the prose. It is blind to the thing that actually breaks a merge send:
the **field values** substituted into ``{{First Name}}`` / ``{{Company}}``. A template
that lints perfectly still ships "Hi 🍦," or "agents at Canopus GBS | SAP Consulting |
AI & Automation | move..." if the CSV carries a scraped LinkedIn headline in ``company``.

Found the hard way on 2026-07-28: 334 rows in ``ready-to-load.csv`` passed the copy gate
with **0 errors across 1,002 renders**, while 9 rows would have sent a visibly broken
email and 70 more read as an obvious mail-merge ("Once agents at MediPath, Inc. move...").

Two responsibilities, deliberately separate:

* :func:`clean_first_name` / :func:`clean_company` — **repair** the mechanical defect
  classes that have a single unambiguous fix (a title prefix, a pipe-delimited headline,
  a trailing legal suffix). Applied at ingestion so the defect never reaches the load file.
* :func:`check_row` — **judge** what is left. A ``block`` finding means the row must not
  reach ``ready-to-load.csv``; guessing a human's name is not a repair this module will
  make up. ``warn`` is advisory and never gates.

Repair is conservative by construction: every helper returns the ORIGINAL value when its
transform would produce something empty, absurdly short, or less informative than what it
started with. A wrong "fix" that silently renames a prospect is worse than a flagged row.

Stdlib-only, tenant-agnostic, no I/O — the same reasons ``slugify.py`` looks the way it does.
"""

from __future__ import annotations

import datetime
import re
import unicodedata
from dataclasses import dataclass

__all__ = [
    "Finding",
    "signal_latest_date",
    "signal_is_fresh",
    "clean_first_name",
    "clean_last_name",
    "clean_company",
    "clean_title",
    "check_row",
    "clean_row",
    "blocks",
    "ends_in_sibilant",
    "starts_with_article",
    "signal_clause",
]


# --- findings ------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One merge-field defect. ``level`` is ``block`` (must not load) or ``warn``."""

    level: str  # "block" | "warn"
    field: str  # "first" | "company" | "email"
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.level.upper()}] {self.field}: {self.rule} — {self.detail}"


# --- first name ----------------------------------------------------------

# Honorifics that arrive glued to the given name ("Dr.rani") or space-separated.
_TITLES = (
    "dr",
    "mr",
    "mrs",
    "ms",
    "miss",
    "prof",
    "professor",
    "sir",
    "rev",
    "capt",
    "lt",
    "sgt",
)
_TITLE_PREFIX_RE = re.compile(
    r"^(?:" + "|".join(_TITLES) + r")\.?\s*(?=[A-Za-zÀ-ÖØ-öø-ÿ])",
    re.IGNORECASE,
)
# Post-nominals that ride along in a name field ("Rothstein, Mba, Pmp").
_CREDENTIAL_RE = re.compile(
    r"[,\s]+(?:mba|md|phd|ph\.d|cpa|pmp|rn|msn|aprn|fnp-?c|cissp|cfa|jd|esq|shrm\s*-?\s*scp|"
    r"cism|cisa|pe|do|dds|mph|mha|bsn|scp|sphr)\b\.?",
    re.IGNORECASE,
)
# A name is letters plus the punctuation real names actually contain.
_NAME_CHARS_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'\-’]*$")
# Values that are placeholders, not people.
_NON_NAMES = frozenset(
    {
        "n/a",
        "na",
        "none",
        "null",
        "unknown",
        "test",
        "team",
        "info",
        "admin",
        "hr",
        "sales",
        "support",
        "contact",
        "hello",
        "there",
        "sir",
        "madam",
        "friend",
        "customer",
        "user",
        "-",
        "--",
        ".",
    }
)


def _has_letters(s: str) -> bool:
    return any(c.isalpha() for c in s)


def _strip_symbols(s: str) -> str:
    """Drop emoji, pictographs, and stray symbol runs, keeping letters/marks/punctuation."""
    return "".join(
        c for c in s if not unicodedata.category(c).startswith(("So", "Sk", "Cs", "Co"))
    ).strip()


def _titlecase(token: str) -> str:
    """Title-case a single name token, preserving interior capitals people actually use
    (McAfee, O'Brien, Jean-Luc) rather than flattening them to Mcafee."""
    if not token:
        return token
    # A two-letter all-caps token is initials (AJ, TJ, JR), not a shouted name. Without
    # this, clean_first_name is NOT idempotent: "A.j." -> "AJ" on the first sweep, then
    # "Aj" on the next one, and consolidate runs on a schedule. A genuinely shouted name
    # ("CHRIS") is 3+ characters and is still normalized.
    if token.isupper() and len(token) <= 2:
        return token
    if token.isupper() or token.islower():
        out = token[:1].upper() + token[1:].lower()
        # Re-capitalize after an internal hyphen or apostrophe.
        return re.sub(r"([\-'’])([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)
    return token


def clean_first_name(first: str, last: str = "", email: str = "") -> str:
    """Return a sendable given name for ``Hi <first>,`` — or ``""`` when none can be
    derived without guessing.

    Repairs, in order: strip symbols/emoji, strip post-nominals, strip an honorific
    prefix (``Dr.rani`` -> ``Jaya``), collapse a glued single-initial prefix
    (``M.omar`` -> ``Ahmer``), normalize a dotted initial pair (``A.j.`` -> ``AJ``),
    take the first token of a multi-word value, and fix casing.

    Falls back to ``last`` then the email local-part **only** when the incoming value
    carries no usable letters at all — the 🍦-in-the-first-name case, where ``last``
    held the real full name. Never invents a name from nothing.
    """
    raw = (first or "").strip()
    cleaned = _strip_symbols(raw)
    cleaned = _CREDENTIAL_RE.sub("", cleaned).strip(" ,.")

    # Nothing usable left. Recover from ``last`` only — that is the swapped-field case,
    # where the real full name sits in the adjacent column ("🍦" / "Marcus Webb").
    #
    # Deliberately NOT falling back to the email local-part: "abrooke@medipath.example" would
    # yield "Hi Abrooke," (an initial glued to a surname), and "j.smith@" would yield
    # "Hi J,". Deriving a human's name from an address is guessing, and this module's
    # contract is that an unrepairable row is flagged for a person, never invented.
    if not _has_letters(cleaned):
        fallback = _strip_symbols((last or "").strip())
        cleaned = fallback if _has_letters(fallback) else ""
    if not cleaned:
        return ""

    cleaned = _TITLE_PREFIX_RE.sub("", cleaned).strip(" .,")

    # "M.omar" -> "Omar": a single initial glued to a real name. Prefer the name.
    # Checked BEFORE the dotted-initials rule below, which would otherwise read the
    # trailing name as a run of initials and return "MAHMER".
    m = re.fullmatch(r"([A-Za-z])\.\s*([A-Za-zÀ-ÖØ-öø-ÿ'\-’]{2,})", cleaned)
    if m:
        cleaned = m.group(2)
    # "A.j." / "A.J" -> "AJ" (initials only). A dot is required, so real two-letter
    # names like "Al" or "Bo" are never uppercased into initials.
    elif re.fullmatch(r"[A-Za-z]\.(?:[A-Za-z]\.?)+", cleaned):
        return re.sub(r"[^A-Za-z]", "", cleaned).upper()

    # Multi-word ("Mary Jane", "Marcus Webb") -> the given name only.
    cleaned = cleaned.split()[0] if cleaned.split() else cleaned
    cleaned = cleaned.strip(" .,")

    if not _has_letters(cleaned):
        return ""
    return _titlecase(cleaned)


def clean_last_name(last: str) -> str:
    """Return a surname safe to render through ``{{Last Name}}``.

    Strips emoji/symbol runs ("Reece 🥇✨") and trailing post-nominals ("Rothstein, Mba,
    Pmp" -> "Rothstein"), then tidies stray punctuation. Accented letters are preserved —
    "Pérez Trufero" is a correct name, not a defect, and must survive untouched.

    ``last`` is not in the current sequence copy, but ``{{Last Name}}`` is a valid provider
    field one template edit away, and ``clean_first_name`` falls back to this column when
    first/last are swapped. Cleaning it closes both paths.
    """
    raw = (last or "").strip()
    if not raw:
        return ""
    out = _strip_symbols(raw)
    prev = None
    while prev != out:  # "Warrix, Cpa, Mba" needs more than one pass
        prev = out
        out = _CREDENTIAL_RE.sub("", out).strip()
    out = re.sub(r"\s+", " ", out).strip(" ,.")
    return out if _has_letters(out) else raw


# --- title ---------------------------------------------------------------

# A LinkedIn headline pasted into the job-title column: "Vice President | Compliance".
# The segments are real information, so they are JOINED, not truncated away — dropping
# everything after the first pipe would silently lose half of someone's role.
_TITLE_PIPE_RE = re.compile(r"\s*[|•·]\s*")


def clean_title(title: str) -> str:
    """Normalize a job title for ``{{Job Title}}`` rendering.

    Converts headline pipe separators to commas ("Vice President | Compliance" ->
    "Vice President, Compliance") and collapses whitespace. Deliberately does NOT
    re-case: job titles arrive in wildly mixed case across sources and guessing at
    capitalization ("head of emea private assets") is a copy decision, not hygiene.
    """
    raw = (title or "").strip()
    if not raw:
        return ""
    out = _TITLE_PIPE_RE.sub(", ", raw)
    out = re.sub(r"\s+", " ", out).strip(" ,")
    return out if _has_letters(out) else raw


# --- company -------------------------------------------------------------

# Trailing legal-entity suffixes. Stripped so the name reads as a company a human would
# say out loud mid-sentence ("agents at Teladoc Health move..."), not as a filing.
_LEGAL_SUFFIX_RE = re.compile(
    r"[,\s]+(?:"
    r"inc|inc\.|incorporated|llc|l\.l\.c\.?|llp|lllp|ltd|ltd\.|limited|"
    r"corp|corp\.|corporation|co|co\.|company|plc|p\.l\.c\.?|"
    r"gmbh|ag|nv|n\.v\.?|bv|b\.v\.?|sa|s\.a\.?|sas|srl|s\.r\.l\.?|spa|s\.p\.a\.?|"
    r"pte|pte\.|pvt|pvt\.|pty|oy|ab|as|a\/s|aps|kk|k\.k\.?|"
    r"lp|l\.p\.?|sc|s\.c\.?|pc|p\.c\.?|pbc|p\.b\.c\.?|"
    r"bancorp|holdings|holding"
    r")\.?$",
    re.IGNORECASE,
)
# Trailing parenthetical qualifiers: "(a Berkley Company)", "(formerly MSys)", "(f.k.a X)".
_PARENTHETICAL_RE = re.compile(r"\s*\((?:[^()]*)\)\s*$")
_TRADEMARK_RE = re.compile(r"[®™©℗]")
# Sentence-ending punctuation *inside* a styled brand ("All About You! Collaborative
# Health Care Services"). Dropped rather than truncated at — truncating would guess
# where the brand ends, and the glyph is the only part that misreads mid-sentence.
_SENTENCE_PUNCT_RE = re.compile(r"[!?]")
_COMPANY_NON_NAMES = frozenset({"n/a", "na", "none", "null", "unknown", "-", "--", "company"})


def clean_company(company: str) -> str:
    """Return a company name safe to drop mid-sentence and to possessivize.

    Repairs: take the head segment of a pipe/bullet-delimited LinkedIn headline, drop
    trademark glyphs, drop a trailing parenthetical qualifier, strip a trailing legal
    suffix, and strip trailing sentence punctuation.

    Every step reverts if it would leave fewer than 2 characters or no letters, so a
    company legitimately *named* e.g. "Co" survives intact.
    """
    raw = (company or "").strip()
    if not raw:
        return ""

    out = raw
    # A scraped headline: "Canopus GBS | SAP Consulting | AI & Automation |" -> head only.
    if "|" in out or "•" in out or "·" in out:
        head = re.split(r"\s*[|•·]\s*", out)[0].strip()
        if len(head) >= 2 and _has_letters(head):
            out = head

    out = _TRADEMARK_RE.sub("", out).strip()
    candidate = re.sub(r"\s+", " ", _SENTENCE_PUNCT_RE.sub("", out)).strip()
    if len(candidate) >= 2 and _has_letters(candidate):
        out = candidate

    prev = None
    while prev != out:
        prev = out
        candidate = _PARENTHETICAL_RE.sub("", out).strip()
        if len(candidate) >= 2 and _has_letters(candidate):
            out = candidate
        candidate = _LEGAL_SUFFIX_RE.sub("", out).strip()
        if len(candidate) >= 2 and _has_letters(candidate):
            out = candidate

    # A dangling conjunction left by suffix removal: "McNeil &" -> "McNeil".
    candidate = re.sub(r"\s*[&+,]\s*$", "", out).strip()
    if len(candidate) >= 2 and _has_letters(candidate):
        out = candidate

    # Trailing sentence punctuation reads as a full stop mid-sentence.
    candidate = out.rstrip(" .!?;:,")
    if len(candidate) >= 2 and _has_letters(candidate):
        out = candidate

    out = re.sub(r"\s+", " ", out).strip()
    return out if _has_letters(out) else raw


# --- signal clause -------------------------------------------------------

# A `why_now` that is really an intent-topic score, not an event: "machine learning &
# artificial intelligence (intent score 81)". A topic score is a targeting input, never a
# thing to tell a prospect you noticed — quoting it back reads as surveillance and says
# nothing dated or specific.
EM_DASH = "\u2014"

_INTENT_LABEL_RE = re.compile(r"\(\s*intent score\s*\d+\s*\)\s*$", re.IGNORECASE)

# Research that explicitly records the ABSENCE of a signal, or one still unverified.
# "No dated funding round or launch event confirmed in research" is a note to ourselves;
# rendering it would open a cold email by telling the prospect we found nothing about them.
_NO_SIGNAL_RE = re.compile(
    r"\b(no dated|no verifiable|not verifiable|no confirmed|no funding|no signal|"
    r"none found|unconfirmed|to confirm|confirm at outreach|tbd|unknown)\b",
    re.IGNORECASE,
)

# Top-level joins between independent facts. A `why_now` packs several facts for the
# operator; an email opens on ONE. Split only outside parentheses so a date range or an
# investor list inside brackets is never cut in half.
_SEGMENT_JOINS = (" + ", " — ", "; ", " then ", ", as ", ", and now ", ", on ")

SIGNAL_MIN_CHARS, SIGNAL_MAX_CHARS = 12, 110
# A segment shorter than this is usually a bare product name ("FinThrive Fusion") with the
# actual news in the NEXT segment. Prefer the first substantive segment over the first one.
SIGNAL_SUBSTANCE_CHARS = 25


def _split_top_level(text: str) -> list[str]:
    """Split on :data:`_SEGMENT_JOINS`, ignoring any join inside parentheses."""
    parts: list[str] = []
    depth = 0
    current = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if depth == 0:
            hit = next((j for j in _SEGMENT_JOINS if text.startswith(j, i)), None)
            if hit:
                parts.append(current)
                current = ""
                i += len(hit)
                continue
        current += ch
        i += 1
    parts.append(current)
    return [p.strip() for p in parts if p.strip()]


def signal_clause(why_now: str) -> str:
    """Reduce a research ``why_now`` to a clause safe to render in a cold email, or ``""``.

    Returns the empty string whenever the value cannot open an email — an intent-topic
    score, a note that no signal was found, an unverified marker, a truncated source, or
    anything that will not fit. **Fail-closed on purpose:** a row with no usable clause
    belongs in the generic sequence, and sending a mangled or absent "why now" is worse
    than sending none.

    The clause is a VERBATIM span of the research, never a paraphrase. Reducing is allowed
    to drop trailing facts; rewording is not, because a reworded claim is a new claim about
    a real company that nobody verified.
    """
    raw = re.sub(r"\s+", " ", (why_now or "").strip())
    if not raw:
        return ""
    if _INTENT_LABEL_RE.search(raw) or _NO_SIGNAL_RE.search(raw):
        return ""

    def _fits(s: str) -> bool:
        return SIGNAL_MIN_CHARS <= len(s) <= SIGNAL_MAX_CHARS

    # Keep the whole value when it already fits — splitting a value that needed no split
    # is how "FinThrive Fusion, agentic AI-powered RCM platform unveiled at HIMSS" gets
    # cut down to a bare product name with the actual news thrown away.
    clause = raw.strip(" ;,.")
    if not _fits(clause):
        segments = [s.strip(" ;,.") for s in _split_top_level(raw)]
        lead = segments[0]
        second = segments[1] if len(segments) > 1 else ""
        if len(lead) < SIGNAL_SUBSTANCE_CHARS and _fits(second):
            # A short lead is a bare product name ("FinThrive Fusion") and the actual news
            # is the next segment.
            clause = second
        elif _fits(lead):
            clause = lead
        else:
            # The lead fact is merely verbose. Trim a trailing parenthetical citation to
            # make it fit — but never fall through to a later segment, which is
            # elaboration and yields a mid-sentence fragment ("scaling LuLu").
            clause = re.sub(r"\s*\([^()]*\)\s*$", "", lead).strip(" ;,.")

    if not _fits(clause):
        return ""
    # An em dash is banned in outreach copy, and a retained segment should never carry one.
    if EM_DASH in clause:
        return ""
    if clause.count("(") != clause.count(")") or clause.count('"') % 2:
        return ""
    if not _has_letters(clause):
        return ""
    # A source cut off mid-thought: no terminal punctuation, no closing bracket, and the
    # reduction changed nothing, so there was no complete lead fact to take.
    if clause == raw and not re.search(r"[.)\"'\d]$|[a-z]$", raw):
        return ""
    return clause


# --- signal freshness ----------------------------------------------------
#
# A SEPARATE concern from :func:`signal_clause`, which judges only whether a value is
# well-formed enough to render. A perfectly-formed clause can still be unsendable because
# it is OLD: touch 1 opens "Saw the news out of {{Company}}: {{Why Now}}." — a present-tense
# recency claim. Found 2026-07-29: of 22 clauses that passed the formatting gate, 9 were
# 8-18 months old and 5 asserted no date at all. "Saw the news out of Gears & Vectors:
# CoreWeave completed its $1.4B acquisition" about something 15 months past reads as
# automated, which is the exact opposite of what a signal is for.
#
# Kept out of ``signal_clause`` on purpose: that function is pure and deterministic, and
# freshness depends on when you ask. Callers pass ``as_of`` so tests pin it.

SIGNAL_MAX_AGE_DAYS = 210  # ~7 months; a funding round that old is history, not news

_MONTH_NAMES = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_YEAR_RE = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{4})\b")


def signal_latest_date(clause: str) -> datetime.date | None:
    """Newest date the clause actually asserts, or ``None`` when it asserts none.

    Reads ISO (``2026-06-09``) and month-year (``May 2026``, ``Sept 2025``) forms, taking
    the NEWEST — a clause may cite both an event and a filing date, and recency is what
    the opener claims. A month-year resolves to the 1st, which is the conservative
    (oldest) reading of that month.

    ``None`` is not "fresh": an undated clause cannot support "saw the news" either, and
    :func:`signal_is_fresh` treats it as a failure.
    """
    found: list[datetime.date] = []
    for year, month, day in _ISO_DATE_RE.findall(clause or ""):
        try:
            found.append(datetime.date(int(year), int(month), int(day)))
        except ValueError:
            continue  # 2026-13-45 and friends: a real string, not a real date
    for name, year in _MONTH_YEAR_RE.findall(clause or ""):
        lowered = name.lower()
        month = _MONTH_NAMES.get(lowered[:4]) or _MONTH_NAMES.get(lowered[:3])
        if month:
            found.append(datetime.date(int(year), month, 1))
    return max(found) if found else None


def signal_is_fresh(
    clause: str,
    as_of: datetime.date | None = None,
    max_age_days: int = SIGNAL_MAX_AGE_DAYS,
) -> bool:
    """Whether ``clause`` is recent enough to open on "saw the news".

    Fail-closed like the rest of this module: no date, an unparseable date, or a date
    older than ``max_age_days`` all return ``False``, which routes the row to the generic
    arc rather than making a recency claim the facts do not support. A future date also
    fails — that is bad research, not fresh news.
    """
    latest = signal_latest_date(clause)
    if latest is None:
        return False
    today = as_of or datetime.date.today()
    age = (today - latest).days
    return 0 <= age <= max_age_days


# --- row-level -----------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_ROLE_LOCAL_RE = re.compile(
    r"^(?:info|sales|support|contact|hello|admin|team|office|enquiries|inquiries|"
    r"help|billing|careers|jobs|press|marketing|noreply|no-reply|postmaster|webmaster)$",
    re.IGNORECASE,
)
_FREEMAIL = frozenset(
    {
        "gmail.com",
        "yahoo.com",
        "hotmail.com",
        "outlook.com",
        "aol.com",
        "icloud.com",
        "protonmail.com",
        "proton.me",
        "gmx.com",
        "mail.com",
        "yandex.com",
        "qq.com",
        "163.com",
    }
)
# Values that mean enrichment failed, not that the company has an odd domain.
_JUNK_DOMAIN_TOKENS = (
    "azurewebsites.net",
    "geo-blocked",
    "linkedin.com",
    "example.com",
    "localhost",
    "notfound",
)


def starts_with_article(company: str) -> bool:
    """True when ``company`` already begins with an article, so a template that writes
    "the {{Company}} stack" renders "the The Meridian Group stack".

    The article is part of the brand ("Silver Path"), so stripping it would be wrong.
    The template is what has to change: "the stack at {{Company}}" reads correctly whether
    or not the name carries its own article — and, unlike the possessive form, whether or
    not it ends in a sibilant.
    """
    return bool(re.match(r"^(the|a|an)\s", (company or "").strip(), re.IGNORECASE))


def ends_in_sibilant(company: str) -> bool:
    """True when ``company`` already ends in s/x/z, so a ``{{Company}}'s`` construction
    renders a double sibilant: "Gears & Vectors's stack", "Vantos's stack".

    Not a data defect — the *template* is what's wrong. A copy that says
    "the stack at {{Company}}" reads correctly for every name in the list.
    """
    return bool(re.search(r"[sxz]$", (company or "").strip(), re.IGNORECASE))


def check_row(row: dict) -> list[Finding]:
    """Judge one prospect row's merge fields **after** :func:`clean_row` has run.

    ``block`` findings mean the row cannot be sent as-is; ``warn`` findings are
    advisory. Reads ``first``, ``last``, ``email``, ``company``, ``company_domain``.
    """
    out: list[Finding] = []
    first = (row.get("first") or "").strip()
    company = (row.get("company") or "").strip()
    email = (row.get("email") or "").strip()
    # removeprefix, NOT lstrip — lstrip("www.") strips a CHARACTER SET and turns
    # "wavelet.example" into "andb.ai", inventing a domain mismatch that isn't there.
    domain = (row.get("company_domain") or "").strip().lower().removeprefix("www.")

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
    if any(tok in domain for tok in _JUNK_DOMAIN_TOKENS):
        out.append(Finding("warn", "company", "company-domain-junk", repr(domain)))

    # -- last name / title: not in today's copy, so advisory here. The merge-render
    # linter escalates these to errors when a template actually uses {{Last Name}} or
    # {{Job Title}} — severity should follow what the copy renders, not what it might.
    last = (row.get("last") or "").strip()
    if last:
        if _strip_symbols(last) != last:
            out.append(Finding("warn", "last", "last-name-symbols", repr(last)))
        if _CREDENTIAL_RE.search(last):
            out.append(Finding("warn", "last", "last-name-credentials", repr(last)))

    title = (row.get("title") or "").strip()
    if title and re.search(r"[|•·]", title):
        out.append(Finding("warn", "title", "title-headline", repr(title)))

    return out


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
    }
    # Only write back fields the caller actually carries — inventing a key would change
    # the row's shape and surprise anything comparing rows or writing a fixed CSV schema.
    out.update({k: val for k, val in cleaned.items() if k in row})
    return out


def blocks(row: dict) -> bool:
    """True when ``row`` carries at least one ``block`` finding — i.e. must not load."""
    return any(f.level == "block" for f in check_row(row))
