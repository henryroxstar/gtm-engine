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
    "clean_segment",
    "SEGMENTS",
    "check_row",
    "clean_row",
    "blocks",
    "ends_in_sibilant",
    "starts_with_article",
    "signal_clause",
    "signal_on_topic",
    "signal_is_event",
    "signal_stray_digits",
    "SIGNAL_TOPIC_TERMS",
    "SIGNAL_EVENT_VERBS",
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
# A LinkedIn *summary* captured into the title field: "Deputy director, treasury, 20 years in
# institutional portfolio management & strategic funding". Detected on the tenure boast, not on
# length — plenty of real C-suite titles run past 70 characters ("Chief Information Security
# Officer & Vice President of Information Security"), so length alone only produces noise.
_TITLE_BIO_RE = re.compile(r"\b\d+\+?\s*(?:years?|yrs?)\b", re.IGNORECASE)


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
# A deal/shell-entity name captured where the operating company belongs, e.g.
# "B. Riley Principal 150 Merger" (a SPAC vehicle, not the employer anyone would recognise).
# It renders mid-sentence — "Once agents at B. Riley Principal 150 Merger move…" — and reads
# as obviously wrong to the recipient, which is the whole cost.
#
# Anchored deliberately narrowly: a trailing "Merger", or an explicit vehicle/shell token.
# A bare "merger" anywhere would false-flag real brands (Mergermarket), so it never matches
# mid-word or mid-name.
_TRANSACTION_ENTITY_RE = re.compile(
    r"(?:\bmerger$|\bmerger\s+(?:corp|sub|co)\b|\bacquisition\s+corp\b|"
    r"\bholdco\b|\bspac\b|\bshell\s+corp\b)",
    re.IGNORECASE,
)


# --- segment -------------------------------------------------------------

#: The canonical segment vocabulary, lowercase. Storage is lowercase and presentation
#: capitalises at the boundary: ``prospects_import._segment_from_size`` already emits
#: lowercase, and ``prospects_import`` already ``.capitalize()``s on the way out to the
#: HubSpot ``GTM_Segment`` column. Titlecase values in a stored CSV are that presentation
#: form leaking back into storage, which is the defect this normalises.
#:
#: Not a judgement about which segment a row belongs to — only about how the value is
#: spelled. ``unspecified`` is a real, kept value: a row whose segment nobody determined
#: is not a startup by default.
SEGMENTS = ("enterprise", "startup", "unspecified")


def clean_segment(segment: str) -> str:
    """Return the canonical lowercase spelling of a segment value.

    Case- and whitespace-only repair, deliberately: anything that is not already one of
    :data:`SEGMENTS` when lowercased is returned UNCHANGED, because mapping an unknown
    value onto a known one would be inventing a segment rather than normalising one.
    That keeps the function safe to run over every row on every sweep.

    Measured on 2026-08-21 across 87 prospect CSVs / 36,210 rows: ``Enterprise`` 17,318,
    ``Startup`` 9,204, ``startup`` 5,488, ``enterprise`` 3,941, ``unspecified`` 259 — a
    2:1 split with no majority convention, which is why a comparison against the hook
    matrix's segment axis cannot simply trust the stored string.
    """
    lowered = (segment or "").strip().lower()
    return lowered if lowered in SEGMENTS else (segment or "").strip()


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


# --- signal relevance ----------------------------------------------------
#
# A THIRD concern, separate from both :func:`signal_clause` (is the value well-formed?)
# and :func:`signal_is_fresh` (is it recent?). A clause can be perfectly formed, dated and
# recent, and still be the wrong clause to open THIS body on.
#
# Found 2026-08-19 spot-checking the Run-500 send: 101 of 453 live rows (22%) carried a
# clause with no AI/agent/automation content at all, under a body whose second paragraph
# claims "agents acting on regulated records at {{Company}}". "D.A. Davidson advises
# fintech and wealth management firms on their sale transactions." followed by an agent
# governance claim is a non-sequitur the recipient reads as a template that ignored what
# it just said, which is the most reliable "this is generated" tell in the campaign.
#
# The terms are a PARAMETER, not a tenant fact: this module stays company-agnostic, and a
# campaign about something other than agents passes its own vocabulary. The default is the
# agentic-AI vocabulary because that is the shape every current pack sells into.

SIGNAL_TOPIC_TERMS: tuple[str, ...] = (
    "ai",
    "a.i.",
    "agent",
    "agents",
    "agentic",
    "automation",
    "automate",
    "automates",
    "automated",
    "autonomous",
    "bot",
    "bots",
    "chatbot",
    "copilot",
    "genai",
    "llm",
    "llms",
    "machine learning",
    "mcp",
    "model",
    "models",
    "assistant",
    "assistants",
    "intelligence",
    # Named products and vendors. A clause can be squarely on topic without ever using a
    # generic word: "Optum deployed Claude, built by Anthropic, across healthcare claims"
    # and "Singlife became the first insurer in Singapore to put Salesforce Agentforce into
    # production" are the strongest openers in the 08-18 list and the first version of this
    # gate suppressed both. Vendor names are the vocabulary buyers actually use.
    "claude",
    "anthropic",
    "chatgpt",
    "openai",
    "codex",
    "copilot",
    "agentforce",
    "bedrock",
    "agentcore",
    "langchain",
    "aidoc",
    "abridge",
    "robotaxi",
    "robotaxis",
    "driverless",
    "self-driving",
    "rpa",
    "digital teammate",
    "digital teammates",
    "digital worker",
    "digital workers",
)

# "AI" glued to the end of a coined name — SinglepointAI, OpenAI, xAI, AIwithCare. The
# whole-word matcher cannot see these, and lowering it to a substring match would fire on
# Dubai, Mumbai and Chennai. Case is what separates them: a capital A-I next to lowercase
# letters is a product name, "ai" inside a place name never is.
_EMBEDDED_AI_RE = re.compile(r"(?:(?<=[a-z])AI\b|\bAI(?=[a-z]))")

# Verbs that make a clause an EVENT (something that happened on a date) rather than a
# standing description of what the company does. Every spec's section 1 claims the clause
# is "event-shaped, not a static capability statement"; 270 of 453 rows (60%) were not.
# Advisory only: a well-chosen standing fact still beats no clause, and promoting this to
# a block would suppress more rows than the campaign can afford to lose.
SIGNAL_EVENT_VERBS: tuple[str, ...] = (
    "launch",
    "launches",
    "launched",
    "ship",
    "ships",
    "shipped",
    "raise",
    "raises",
    "raised",
    "acquire",
    "acquires",
    "acquired",
    "partner",
    "partners",
    "partnered",
    "name",
    "names",
    "named",
    "appoint",
    "appoints",
    "appointed",
    "expand",
    "expands",
    "expanded",
    "announce",
    "announces",
    "announced",
    "hire",
    "hires",
    "hiring",
    "sign",
    "signs",
    "signed",
    "close",
    "closes",
    "closed",
    "deploy",
    "deploys",
    "deployed",
    "roll",
    "rolls",
    "rolled",
    "standardise",
    "standardises",
    "standardize",
    "standardizes",
    "trial",
    "trials",
    "trialing",
    "trialling",
    "pilot",
    "pilots",
    "piloting",
    "invest",
    "invests",
    "invested",
    "select",
    "selects",
    "selected",
    "integrate",
    "integrates",
    "integrated",
    "introduce",
    "introduces",
    "introduced",
    "debut",
    "debuts",
    "debuted",
    "open",
    "opens",
    "opened",
    "add",
    "adds",
    "added",
    "unveil",
    "unveils",
    "unveiled",
    "build",
    "builds",
    "building",
    "credit",
    "credits",
    "credited",
    "settle",
    "settles",
    "settled",
)


def _term_re(terms: tuple[str, ...]) -> re.Pattern[str]:
    """Whole-word alternation over ``terms``, longest first so "machine learning" wins."""
    ordered = sorted({t.strip().lower() for t in terms if t.strip()}, key=len, reverse=True)
    return re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(t) for t in ordered) + r")(?!\w)",
        re.IGNORECASE,
    )


_TOPIC_RE = _term_re(SIGNAL_TOPIC_TERMS)
_EVENT_RE = _term_re(SIGNAL_EVENT_VERBS)


def signal_on_topic(clause: str, terms: tuple[str, ...] = SIGNAL_TOPIC_TERMS) -> bool:
    """True when ``clause`` mentions at least one of ``terms``.

    Whole-word matched, so "AI" does not fire on "Dubai" and "model" does not fire on
    "remodelled". A clause that fails this cannot open a body that then claims something
    about the recipient's agents: the two paragraphs do not connect and the reader sees
    the seam.
    """
    if not (clause or "").strip():
        return False
    rx = _TOPIC_RE if terms is SIGNAL_TOPIC_TERMS else _term_re(terms)
    if rx.search(clause):
        return True
    return terms is SIGNAL_TOPIC_TERMS and bool(_EMBEDDED_AI_RE.search(clause))


def signal_is_event(clause: str) -> bool:
    """True when ``clause`` carries a verb that makes it a dated event rather than a
    standing description of the company.

    Advisory input, never a block: "Mayo Clinic runs NVIDIA Blackwell infrastructure to
    power its foundation model and agentic AI program" has no event verb and is still a
    good opener. Use it to rank re-research, not to suppress.
    """
    if not (clause or "").strip():
        return False
    return bool(_EVENT_RE.search(clause))


def signal_stray_digits(clause: str, company: str = "") -> list[str]:
    """Digit-bearing tokens in ``clause`` that are NOT part of ``company``'s own name.

    The clause contract has always said "no digits". The intent was never to ban the
    numeral itself: it was to keep a **date or a metric** out of an opener, because
    "raised $45M in March" reads as a scraped record rather than something a person
    noticed. Written as a blanket ban it also excluded any company whose *name* contains a
    digit, which is not a defect at all.

    Found 2026-08-19: G2 Risk Solutions and B2Gnow both had a verified, well-sourced AI
    trigger and no sendable clause, because the contract required the clause to open on the
    company name and simultaneously forbade the digit inside it. Two rules, jointly
    unsatisfiable, and neither was ever enforced in code — the ban lived only in prose in
    the spec files, so nothing caught the collision or the dates it was written to stop.

    Returns the offending tokens (empty when the clause is clean), so a caller can name
    them rather than saying "no digits allowed" at a clause whose only digit is a brand.
    """
    text = (clause or "").strip()
    if not text:
        return []
    allowed = {t.lower() for t in re.findall(r"[\w.'-]*\d[\w.'-]*", company or "")}
    return [t for t in re.findall(r"[\w.'-]*\d[\w.'-]*", text) if t.lower() not in allowed]


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
    # A landing-page URL parked in a domain column. Providers do this routinely
    # ("sc.com/sg", "kpmg.com/us", "optum.com/en"): the host is right, the field is
    # wrong. Kept as junk so the data defect stays visible even though
    # :func:`bare_host` now stops it inventing a domain mismatch.
    "/",
)

_URLISH_RE = re.compile(r"^\s*[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def bare_host(value: str) -> str:
    """A ``company_domain`` field reduced to the host it actually names.

    Strips a scheme, ``www.``, any path/query/fragment, a port, and a trailing dot —
    so ``"https://www.sc.com/sg"`` and ``"sc.com"`` compare equal.

    Why this exists: providers park a landing-page URL in the domain column, and the
    comparison it feeds (``email-domain-mismatch``) then reports ``sc.com != sc.com/sg``
    — the host matches, only the field is dirty. Measured 2026-08-20 on the live lists:
    **every** non-bare-host value produced a false mismatch, 10 of 52 mismatch findings
    in total. A gate that cries wolf on a fifth of its own findings trains a reader to
    skim all of them.

    Normalising for comparison must not hide the defect, so the raw value still trips
    ``company-domain-junk`` via the ``"/"`` token above: right finding, right name.
    """
    v = (value or "").strip().lower()
    v = _URLISH_RE.sub("", v)
    v = v.removeprefix("www.")
    # Cut at the first path/query/fragment character, then drop any :port.
    v = re.split(r"[/?#]", v, maxsplit=1)[0]
    v = v.split(":", 1)[0]
    return v.strip().rstrip(".")


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
    # Compare on the HOST, never the raw field. Two normalisation bugs have invented a
    # domain mismatch here: lstrip("www.") stripped a CHARACTER SET and turned
    # "wavelet.example" into "andb.ai"; and a bare removeprefix left a provider's
    # landing-page path in place, so "sc.com/sg" never equalled "sc.com". The raw value
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
        "segment": clean_segment(row.get("segment", "")),
    }
    # Only write back fields the caller actually carries — inventing a key would change
    # the row's shape and surprise anything comparing rows or writing a fixed CSV schema.
    out.update({k: val for k, val in cleaned.items() if k in row})
    return out


def blocks(row: dict) -> bool:
    """True when ``row`` carries at least one ``block`` finding — i.e. must not load."""
    return any(f.level == "block" for f in check_row(row))


def main(argv: list[str] | None = None) -> int:
    """Bucket a prospect CSV by whether each row's clause can open a given campaign body.

    Deliberately runnable BEFORE any copy exists. The merge-render linter reports the same
    defect, but only once a spec is written — by which point the body has usually already
    been softened to fit the weakest clause in the list, which is the wrong repair. This
    decides LIST COMPOSITION: re-research the row, or send it a sequence about something
    it is actually about.

    Reads the ``signal_clause`` column (the sendable clause), never ``why_now`` (the
    multi-fact research note) — the two are confusable and only one ever renders.
    """
    import argparse
    import csv
    import sys

    ap = argparse.ArgumentParser(prog="python -m gtm_core.merge_hygiene")
    ap.add_argument("csv_path", help="prospect CSV carrying a signal_clause column")
    ap.add_argument(
        "--terms",
        help="comma-separated campaign vocabulary (default: the agentic-AI set)",
    )
    ap.add_argument(
        "--column", default="signal_clause", help="clause column (default: signal_clause)"
    )
    ap.add_argument(
        "--include-suppressed",
        action="store_true",
        help="also judge rows already carrying a suppression reason",
    )
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    terms = (
        tuple(t.strip() for t in args.terms.split(",") if t.strip())
        if args.terms
        else SIGNAL_TOPIC_TERMS
    )
    with open(args.csv_path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not args.include_suppressed:
        rows = [r for r in rows if not (r.get("suppression") or "").strip()]
    if not rows:
        print("no live rows", file=sys.stderr)
        return 2

    off = [r for r in rows if not signal_on_topic(r.get(args.column) or "", terms)]
    static = [
        r
        for r in rows
        if signal_on_topic(r.get(args.column) or "", terms)
        and not signal_is_event(r.get(args.column) or "")
    ]
    print(f"{len(rows)} live rows in {args.csv_path}")
    print(f"  on topic     {len(rows) - len(off):>4}  ({(len(rows) - len(off)) / len(rows):.0%})")
    print(f"  OFF TOPIC    {len(off):>4}  ({len(off) / len(rows):.0%})  <- re-research or suppress")
    print(f"  no event verb{len(static):>4}  ({len(static) / len(rows):.0%})  (advisory)")
    for r in off:
        print(f"    {(r.get('email') or '?').strip():<40} {(r.get(args.column) or '')[:70]}")
    return 1 if off else 0


if __name__ == "__main__":
    raise SystemExit(main())
