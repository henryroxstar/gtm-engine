"""The research record behind a prospect row — provenance and verdict as *data*.

Before this module, a row's whole claim to being researched was one free-text column,
``signal_clause``, whose contract (``merge_hygiene.signal_clause``) deliberately
**forbids dates and digits** so the opener reads as a sentence rather than a data dump.
That contract is right for the prose and catastrophic for verification: with no date,
no number, no source and no named subject anywhere on the row, three whole defect
classes were undetectable *by construction* —

* a clause naming a company that had been renamed years earlier (the source was never
  recorded, so nothing could re-check it);
* a clause about company A opening an email to company B, because the fact's real
  subject was never written down;
* a number that drifted across three documents until it described a mechanism the
  original source never measured — each hop plausible, the endpoint false.

No amount of copy review finds these reliably. They are not judgement calls: they
are **type errors** once the row carries the fields to check against.

Six provenance fields plus a verdict:

``signal_source_url``   where the fact came from — https URL, one source, not a search page.
``signal_observed``     ISO date the source published/showed it. Freshness moves HERE,
                        off the clause, which resolves the "clause must be dated / clause
                        may not contain a date" contradiction rather than living with it.
``signal_evidence``     a verbatim span of the source that supports the clause. Not a
                        summary — the thing the clause is a reduction *of*.
``signal_subject``      the entity the fact is about. Usually the row's company; when it
                        is not, that is the defect, stated as data.
``signal_agent_kind``   what the word "agent" refers to in this clause: ``ai`` | ``human``
                        | ``none`` | ``unclear``. An insurance carrier's "agents" are
                        people; a staffing firm's are recruiters. Both read as an AI-agent
                        signal to a regex and to a hurried human.
``category_relation``   what this account is to us: ``prospect`` | ``competitor`` |
                        ``partner`` | ``adjacent`` | ``unclear``.
``verdict``             ``send`` | ``re-angle`` | ``drop`` — plus ``verdict_reason``.
                        "Not sendable" is a legitimate, first-class output of research.
                        Without it, a research step asked for 442 emails produces 442.

Fail-closed like every other gate here: ``unclear``, blank, unparseable, and stale all
fail. A row whose provenance nobody recorded is a row whose claim nobody can check.

**Migration.** A list written before these columns existed produces ONE file-level
finding, not one per row, avoiding unreadable output flooding.

Stdlib-only, no I/O, tenant-agnostic.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

from .merge_hygiene import SIGNAL_MAX_AGE_DAYS, Finding, clean_company
from .merge_hygiene.signal_clean import signal_clause

__all__ = [
    "AgentKind",
    "CategoryRelation",
    "Verdict",
    "SIGNAL_RECORD_COLUMNS",
    "VERDICT_COLUMNS",
    "RECORD_COLUMNS",
    "JUDGE_COLUMNS",
    "HOOK_CELL_COLUMN",
    "SIGNAL_COLUMN",
    "RecordAudit",
    "has_record_columns",
    "missing_record_columns",
    "normalise_company",
    "content_tokens",
    "claim_numbers",
    "evidence_supports",
    "check_record",
    "audit_records",
]


class AgentKind:
    """What the word "agent" denotes in this row's clause."""

    AI = "ai"
    #: Insurance agents, travel agents, real-estate agents, recruiters. People.
    HUMAN = "human"
    #: The clause does not use the word at all — the common, correct case for a
    #: funding round or a leadership hire.
    NONE = "none"
    UNCLEAR = "unclear"


class CategoryRelation:
    """What this account is to us. Decided during research, not at send time."""

    PROSPECT = "prospect"
    COMPETITOR = "competitor"
    PARTNER = "partner"
    #: Ships something neighbouring — not a competitor, not a partner, and a cold
    #: pitch that ignores the overlap reads as not having looked.
    ADJACENT = "adjacent"
    #: A supervisory or standards body — it WRITES the rules the pitch appeals to.
    #: Added 2026-08-21 after the Monetary Authority of Singapore was researched, recorded
    #: ``prospect``, given ``verdict: send``, and staged into the exec list on a clause
    #: describing MAS's *own* published agentic-AI safeguards. The body then argued that
    #: governance unlocks an enterprise deal. Nothing objected: the vocabulary had no word
    #: for "this account is the regulator", so the only available answer was ``prospect``,
    #: and a fail-closed field cannot fail closed on a value it cannot represent.
    #: This is the same fix as :class:`Verdict` itself — a defect becomes detectable only
    #: once the record can say it.
    REGULATOR = "regulator"
    UNCLEAR = "unclear"


class Verdict:
    SEND = "send"
    #: The account is real and the seat is right, but this clause cannot carry the
    #: pitch. Goes back to research, not to the sequencer.
    REANGLE = "re-angle"
    DROP = "drop"


SIGNAL_RECORD_COLUMNS = (
    "signal_source_url",
    "signal_observed",
    "signal_evidence",
    "signal_subject",
    "signal_agent_kind",
    "category_relation",
)
VERDICT_COLUMNS = ("verdict", "verdict_reason")

#: The matrix cell this row's own segment + observed signal put it in, recorded at RESEARCH
#: time by the `prospect` skill rather than asserted at drafting time by the spec.
#:
#: Deliberately NOT in :data:`RECORD_COLUMNS`. That tuple defines "this list carries the
#: record at all", and a list missing any of it produces one file-level ERROR — so folding
#: this in would retroactively invalidate every list written before 2026-08-21 for a field
#: none of them could have had. It is checked where it is present and reported as absent
#: where it is not, which is the same migration shape `premise:` takes on the spec side.
#:
#: Why it exists: `hook_coverage`'s `cell-segment-fit` / `cell-signal-fit` are heuristics
#: precisely because only ONE side of the comparison is declared. The spec says which cell it
#: implements; nothing says which cell the ROW belongs to, so the check has to infer the
#: row's side from free text. Recording it upstream turns both checks into an equality test —
#: and, more importantly, moves the defect to where it is born. `prospect` already picks an
#: opening hook per account from the matrix and then throws away which cell it picked.
HOOK_CELL_COLUMN = "hook_cell"

#: The matrix SIGNAL column (only) this row's own observed why-now attests, written verbatim
#: in the matrix's own label for the row's segment grid — e.g. ``"Compliance event (audit,
#: breach)"``. Same tier as :data:`HOOK_CELL_COLUMN` and deliberately NOT in
#: :data:`RECORD_COLUMNS`, for the identical reason: folding it in would retroactively
#: invalidate every list written before this column existed.
#:
#: Why this is recorded instead of ``hook_cell`` directly: the PERSONA half of a cell is
#: already derivable from the row's own `title` (``outreach_pack_linter.persona_of``) and its
#: SEGMENT is already an enumerated column, so a row that also hand-writes `hook_cell` is
#: stating two independent facts (persona, signal) as one hand-typed string that can drift
#: from the title that names the persona. Measured 2026-08-23: of the profile's eval drafts,
#: the ones written with a hand-filled `hook_cell` already regressed to leaving it blank one
#: day later. Recording only the non-derivable atom — which signal was observed — and letting
#: `gtm_core.hook_coverage.derive_row_cell` compute the cell from
#: ``persona_of(title) x segment x signal_column`` removes that drift class by construction:
#: there is no second copy of the persona to disagree with the title.
SIGNAL_COLUMN = "signal_column"

#: The MACHINE's opinion of a row, kept in its own columns so it can never be mistaken
#: for the researcher's.
#:
#: ``verdict`` above is research-owned: written once by `prospect`, from evidence, and
#: never machine-overwritten. Until 2026-08-27 the judge wrote into that same column, so
#: a judge ``send`` silently erased a research ``re-angle`` or ``drop`` and the enrollment
#: gate then admitted the row. Three documents asserted "the judge ranks and never
#: blocks"; the artifact did the opposite, because one column had two writers and the
#: later one won.
#:
#: ``judge_calibrated`` is deliberately tri-state — ``"true"`` / ``"false"`` / ``""`` for
#: never-checked — preserving the None-vs-False distinction on disk. "Nobody asked whether
#: this judge has been measured" and "we asked, and it has not been" are different facts,
#: and only the second is evidence of anything.
#:
#: Deliberately NOT in :data:`RECORD_COLUMNS`, on the same reasoning as
#: :data:`SIGNAL_COLUMN`: that tuple defines "this list carries the research record at
#: all", so folding these in would retroactively fail every list written before a judge
#: existed, for fields none of them could have had.
#:
#: ``judge_defect_class`` (added 2026-09-03) is the NORMALISED class — the routing key.
#: ``judge_verdict_reason`` prefers the evidence phrase, so on real lists it is mostly
#: email-body text and cannot say whether a drop meant "wrong argument" or "wrong company".
JUDGE_COLUMNS = ("judge_verdict", "judge_verdict_reason", "judge_calibrated", "judge_defect_class")

RECORD_COLUMNS = SIGNAL_RECORD_COLUMNS + VERDICT_COLUMNS


def has_record_columns(fieldnames: list[str] | tuple[str, ...] | None) -> bool:
    """Whether a CSV header carries the record at all (vs. predating it)."""
    return bool(fieldnames) and all(c in set(fieldnames or ()) for c in RECORD_COLUMNS)


def missing_record_columns(fieldnames: list[str] | tuple[str, ...] | None) -> list[str]:
    present = set(fieldnames or ())
    return [c for c in RECORD_COLUMNS if c not in present]


# --- source URL ----------------------------------------------------------

_HTTPS_RE = re.compile(r"^https://[^\s/]+\.[a-z]{2,}(?:/|$)", re.IGNORECASE)
# A results page is not a source: it is where you went looking. It also rots — the
# same query returns something else next month, so the claim becomes uncheckable
# exactly when someone tries to check it.
_SEARCH_PAGE_RE = re.compile(
    r"(?:google\.[a-z.]+/search|bing\.com/search|duckduckgo\.com/\?|/search\?|"
    r"linkedin\.com/search|news\.google\.com/search)",
    re.IGNORECASE,
)


# --- observed date -------------------------------------------------------

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_observed(value: str) -> datetime.date | None:
    v = (value or "").strip()
    if not _ISO_RE.match(v):
        return None
    try:
        return datetime.date.fromisoformat(v)
    except ValueError:
        return None


# --- subject identity ----------------------------------------------------

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalise_company(name: str) -> str:
    """Reduce a company name to a comparable identity token.

    Reuses :func:`gtm_core.merge_hygiene.clean_company` for legal-suffix and
    headline stripping rather than re-deriving that logic, then flattens what is
    left. "Vertex Systems, Inc." and "Vertex Systems" compare equal; "Vertex
    Systems" and "Halden Rail" do not.
    """
    return _NON_ALNUM_RE.sub("", clean_company(name or "").lower())


#: Trailing words that describe WHAT a company is rather than WHICH company it is. A source
#: names a company the way people say it — "Halden's efforts", "Halden ClaimsDesk" — while the
#: row carries the filing-style "Halden Systems". Used only on the subject checks below; never
#: applied to the rendered name (``clean_company`` owns that, and stays legal-suffix-only).
#: Kept small and explicit: every word added here widens what counts as "the same company".
_DESCRIPTOR_WORDS = frozenset(
    """
    ai card center centre clinical communications companies financial group health
    healthcare holdings insurance system systems technologies technology
    """.split()
)
#: Legal forms. After the FULL name they are that name's own filing form ("Halden Inc." —
#: ``normalise_company`` already equates the two); after a shortened core they are a
#: namesake's ("Acme plc" is not "Acme Financial").
_LEGAL_FORMS = frozenset(
    """
    ag bv co company corp corporation gmbh inc incorporated limited llc llp lp ltd nv plc
    pte pty sa
    """.split()
)
#: A word that, written straight after a name, makes it a DIFFERENT entity's name unless the
#: subject carries it too: "Acme Insurance" is not "Acme Health", "Acme Capital" not "Acme".
_QUALIFIER_WORDS = (
    _DESCRIPTOR_WORDS | _LEGAL_FORMS | frozenset("bancorp bank capital partners".split())
)
_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def _name_forms(name: str) -> list[list[str]]:
    """The full name, then its core: a leading "The" and trailing descriptors dropped.

    Never empty for a non-empty name — the last word always survives. A core under three
    characters is not offered: too short to identify anything."""
    words = _WORD_RE.findall(clean_company(name or "").lower())
    if len(words) > 1 and words[0] == "the":
        words = words[1:]
    core = list(words)
    while len(core) > 1 and core[-1] in _DESCRIPTOR_WORDS:
        core = core[:-1]
    forms = [words] if words else []
    if core != words and len("".join(core)) >= 3:
        forms.append(core)
    return forms


def _company_core(name: str) -> str:
    forms = _name_forms(name)
    return "".join(forms[-1]) if forms else ""


def _named_in(name: str, evidence: str) -> bool:
    """Does the evidence name THIS company — full name or core — and not a namesake?

    Conservative by design: a gate that passes a wrong-company row is worse than one that
    warns on a right one. Every occurrence must be word-bounded and start capitalised, and
    the word after it decides:

    * a possessive (``'s``/``’s``) — accepted;
    * a dot straight after the name, with no space ("acme.ai", "acme.io", "Acme.com") —
      rejected: a domain-style name is a namesake ("acme.ai" is not "Acme Card");
    * a qualifier written straight after it (a space or "&" between) that the subject does
      not itself carry — rejected ("Acme Insurance announced" is another company when the
      subject is "Acme Health"; so is "Acme plc" for "Acme Financial"). A legal form after
      the FULL name is its own ("Acme Inc."). Across punctuation the next word belongs to
      something else: "Bank of Acme, Harbor Capital" names Bank of Acme;
    * a one-word CORE (a name shortened to its first word) additionally needs a capitalised
      non-qualifier after it ("Halden ClaimsDesk"). A bare shortened word is how a
      sentence-initial dictionary word ("Cascade of alerts", "First, the bank") or a
      same-named stranger ("Acme announced") would read as the company. The full name, even
      one word long, is the name the row itself carries, so it does not need this.

    Accepted residual (2026-09-25): a ONE-word company name that is also a dictionary word
    passes on a sentence-initial use ("Level of adoption…" for a company named "Level"),
    because removing the full-name exemption added ~52 budgeted warnings per batch on rows
    that DO name the company. Pinned by a test so tightening it later is a decision.
    """
    carried = set(_WORD_RE.findall((name or "").lower()))
    toks = list(_WORD_RE.finditer(evidence or ""))
    low = [t.group().lower() for t in toks]
    forms = _name_forms(name)
    for k, form in enumerate(forms):
        n = len(form)
        is_full = k == 0
        one_word_core = not is_full and n == 1
        for i in range(len(toks) - n + 1):
            first = toks[i].group()[0]
            if low[i : i + n] != form or not (first.isupper() or first.isdigit()):
                continue
            nxt = toks[i + n] if i + n < len(toks) else None
            gap = evidence[toks[i + n - 1].end() : nxt.start()] if nxt else ""
            if nxt and gap in ("'", "’") and low[i + n] == "s":
                return True
            if nxt and gap == ".":
                continue
            joined = nxt is not None and gap.strip() in ("", "&")
            word = low[i + n] if joined else ""
            own_legal_form = is_full and word in _LEGAL_FORMS
            if word in _QUALIFIER_WORDS and word not in carried and not own_legal_form:
                continue
            if not one_word_core:
                return True
            capitalised_next = joined and gap.strip() == "" and nxt.group()[0].isupper()
            if capitalised_next and word not in _QUALIFIER_WORDS:
                return True
    return False


# --- evidence support ----------------------------------------------------

_STOPWORDS = frozenset(
    """
    about after also been before being between both came come could does done down
    each even ever from give goes going have here into just like made make many
    more most much must near need next only other over said same seen shall since
    some such take than that their them then there these they this those through
    time upon used uses using very want ways well were what when where which while
    with would your
    """.split()
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
#: Below this, the clause is asserting things the quoted source does not say.
EVIDENCE_SUPPORT_THRESHOLD = 0.6
#: How much of a word has to match. Long enough that "examiner" and "example" stay
#: apart, short enough that "verification"/"verified" and "acquires"/"acquisition"
#: do not read as different claims.
_STEM = 5


def content_tokens(text: str) -> list[str]:
    """Meaning-bearing tokens: length >= 4, not a stopword, lowercased."""
    return [
        t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) >= 4 and t not in _STOPWORDS
    ]


_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def claim_numbers(text: str) -> set[str]:
    """Every number a text asserts, normalised so "1,200" and "1200" are one claim.

    A number is the part of a claim that survives paraphrase and travels furthest
    from its source: it gets copied into a guidance doc, then into copy, keeping its
    digits while quietly changing what it measures.
    """
    out = set()
    for raw in _NUMBER_RE.findall(text or ""):
        v = raw.replace(",", "").rstrip(".")
        if v:
            out.add(v.rstrip("0").rstrip(".") if "." in v else v)
    return out


def _stems(tokens: list[str]) -> set[str]:
    return {t[:_STEM] if len(t) >= _STEM else t for t in tokens}


def evidence_supports(
    clause: str, evidence: str, *, threshold: float = EVIDENCE_SUPPORT_THRESHOLD
) -> tuple[bool, list[str], set[str]]:
    """Does the quoted source span actually support the clause?

    Returns ``(supported, unsupported_words, unsourced_numbers)``.

    Two independent tests, because they catch different drift:

    * **word support** — what share of the clause's meaning-bearing words appear in
      the evidence. Catches a clause that has quietly acquired a subject, a verb, or
      a qualifier the source never had.
    * **number provenance** — every number in the clause must appear in the evidence,
      with no threshold. This is not a similarity heuristic; a number the source does
      not contain is fabricated, however plausible the sentence around it.

    Deliberately *not* semantic. A paraphrase that shares no vocabulary with its
    source fails here, and that is the intended trade: the clause contract already
    requires a verbatim reduction of our own research, so vocabulary drift IS the
    defect, not an artefact of measuring it this way.
    """
    c_tokens = content_tokens(clause)
    if not c_tokens:
        return True, [], set()
    e_stems = _stems(content_tokens(evidence))
    unsupported = [t for t in c_tokens if (t[:_STEM] if len(t) >= _STEM else t) not in e_stems]
    share = 1.0 - (len(unsupported) / len(c_tokens))
    unsourced = claim_numbers(clause) - claim_numbers(evidence)
    # Preserve first-seen order, drop duplicates, so the finding names each word once.
    seen: set[str] = set()
    ordered = [t for t in unsupported if not (t in seen or seen.add(t))]
    return (share >= threshold and not unsourced), ordered, unsourced


# --- agent homonym -------------------------------------------------------

_AGENT_WORD_RE = re.compile(r"\bagent(?:s|ic)?\b", re.IGNORECASE)


# --- the per-row check ---------------------------------------------------


def check_record(row: dict, as_of: datetime.date | None = None) -> list[Finding]:
    """Validate one row's research record. Empty list means the record checks out.

    Reuses :class:`gtm_core.merge_hygiene.Finding` so a caller can merge these with
    ``check_row``'s output and treat both as one stream — there is no second finding
    type to teach every downstream gate about.

    A row with no clause and no why_now is a generic-arc row: it makes no dated claim, so
    provenance fields have nothing to be provenance for, and only the verdict is required.
    `signal_clause` is derived from `why_now` when the row carries no stored clause.
    """
    out: list[Finding] = []
    today = as_of or datetime.date.today()
    why_now = (row.get("why_now") or "").strip()
    clause = (row.get("signal_clause") or signal_clause(why_now)).strip()
    company = (row.get("company") or "").strip()

    verdict = (row.get("verdict") or "").strip().lower()
    if not verdict:
        out.append(
            Finding("block", "verdict", "verdict-missing", "no send/re-angle/drop verdict on file")
        )
    elif verdict not in {Verdict.SEND, Verdict.REANGLE, Verdict.DROP}:
        out.append(Finding("block", "verdict", "verdict-unknown", f"{verdict!r} is not a verdict"))
    elif verdict != Verdict.SEND and not (row.get("verdict_reason") or "").strip():
        out.append(
            Finding(
                "block",
                "verdict",
                "verdict-reason-missing",
                f"verdict {verdict!r} with no reason — the reason is what makes it re-checkable",
            )
        )

    relation = (row.get("category_relation") or "").strip().lower()
    if not relation or relation == CategoryRelation.UNCLEAR:
        out.append(
            Finding(
                "block",
                "category_relation",
                "relation-unresolved",
                f"{company!r} has no resolved relation (prospect/competitor/partner/adjacent)",
            )
        )
    elif relation == CategoryRelation.COMPETITOR:
        out.append(
            Finding(
                "block",
                "category_relation",
                "relation-competitor",
                f"{company!r} is recorded as a competitor — never a cold-pitch target",
            )
        )
    elif relation == CategoryRelation.REGULATOR:
        out.append(
            Finding(
                "block",
                "category_relation",
                "relation-regulator",
                f"{company!r} is a supervisory/standards body — it writes the rules this "
                f"pitch appeals to; a commercial cold pitch misreads its role",
            )
        )
    elif relation == CategoryRelation.PARTNER:
        out.append(
            Finding(
                "warn",
                "category_relation",
                "relation-partner",
                f"{company!r} is a partner — a cold outbound pitch is the wrong motion",
            )
        )
    elif relation == CategoryRelation.ADJACENT:
        out.append(
            Finding(
                "warn",
                "category_relation",
                "relation-adjacent",
                f"{company!r} ships something neighbouring — say so, or the pitch reads as "
                f"not having looked",
            )
        )

    if not clause and not why_now:
        return out  # generic arc: no claim, nothing to source

    if why_now and not clause:
        out.append(
            Finding(
                "block",
                "why_now",
                "signal-clause-underivable",
                f"why_now is populated ({why_now[:60]!r}) but cannot be reduced to a clean signal clause",
            )
        )

    url = (row.get("signal_source_url") or "").strip()
    if not url:
        out.append(
            Finding("block", "signal_source_url", "signal-source-missing", "clause has no source")
        )
    elif not _HTTPS_RE.match(url):
        out.append(
            Finding(
                "block",
                "signal_source_url",
                "signal-source-malformed",
                f"{url!r} is not an https URL",
            )
        )
    elif _SEARCH_PAGE_RE.search(url):
        out.append(
            Finding(
                "block",
                "signal_source_url",
                "signal-source-is-search",
                f"{url!r} is a search results page, not the source that carries the fact",
            )
        )

    observed = _parse_observed(row.get("signal_observed") or "")
    if observed is None:
        out.append(
            Finding(
                "block",
                "signal_observed",
                "signal-observed-missing",
                f"{(row.get('signal_observed') or '')!r} is not an ISO date (YYYY-MM-DD)",
            )
        )
    else:
        age = (today - observed).days
        if age < 0:
            out.append(
                Finding(
                    "block",
                    "signal_observed",
                    "signal-observed-future",
                    f"{observed} is in the future",
                )
            )
        elif age > SIGNAL_MAX_AGE_DAYS:
            out.append(
                Finding(
                    "block",
                    "signal_observed",
                    "signal-stale",
                    f"observed {observed} — {age} days old, past the {SIGNAL_MAX_AGE_DAYS}-day limit",
                )
            )

    evidence = (row.get("signal_evidence") or "").strip()
    if not evidence:
        out.append(
            Finding(
                "block",
                "signal_evidence",
                "signal-evidence-missing",
                "clause has no verbatim source span behind it",
            )
        )
    else:
        ok, unsupported, unsourced = evidence_supports(clause or why_now, evidence)
        if unsourced:
            out.append(
                Finding(
                    "block",
                    "signal_evidence",
                    "signal-number-unsourced",
                    f"clause asserts {sorted(unsourced)} — absent from the quoted source",
                )
            )
        if not ok and not unsourced:
            out.append(
                Finding(
                    "block",
                    "signal_evidence",
                    "signal-evidence-unsupported",
                    f"clause asserts words the source span does not: {unsupported[:8]}",
                )
            )

    subject = (row.get("signal_subject") or "").strip()
    if not subject:
        out.append(
            Finding(
                "block",
                "signal_subject",
                "signal-subject-missing",
                "nothing records who the fact is about",
            )
        )
    else:
        s_key, c_key = normalise_company(subject), normalise_company(company)
        if company and s_key != c_key and s_key == _company_core(company):
            # "Tidewater" for "Tidewater Financial" is usually the same company and sometimes
            # a namesake; the record cannot tell which, so a person confirms it. Never silent.
            out.append(
                Finding(
                    "warn",
                    "signal_subject",
                    "signal-subject-short-form",
                    f"the recorded subject {subject!r} is a shorter form of {company!r} — "
                    f"confirm it is the same company",
                )
            )
        elif company and s_key != c_key:
            out.append(
                Finding(
                    "block",
                    "signal_subject",
                    "signal-subject-mismatch",
                    f"fact is about {subject!r}, the email goes to {company!r}",
                )
            )
        if evidence and not _named_in(subject, evidence):
            out.append(
                Finding(
                    "warn",
                    "signal_subject",
                    "signal-subject-absent-from-evidence",
                    f"{subject!r} does not appear in the quoted source span",
                )
            )

    kind = (row.get("signal_agent_kind") or "").strip().lower()
    uses_agent_word = bool(_AGENT_WORD_RE.search(clause or why_now))
    if not kind or kind == AgentKind.UNCLEAR:
        out.append(
            Finding(
                "block",
                "signal_agent_kind",
                "agent-kind-unresolved",
                "clause not classified ai/human/none — the homonym is the whole risk",
            )
        )
    elif kind not in {AgentKind.AI, AgentKind.HUMAN, AgentKind.NONE}:
        out.append(
            Finding(
                "block", "signal_agent_kind", "agent-kind-unknown", f"{kind!r} is not an agent kind"
            )
        )
    elif kind == AgentKind.HUMAN:
        out.append(
            Finding(
                "block",
                "signal_agent_kind",
                "agent-kind-human",
                f"{company!r}'s 'agents' are people — an AI-agent pitch off this clause misreads it",
            )
        )
    elif kind == AgentKind.NONE and uses_agent_word:
        out.append(
            Finding(
                "block",
                "signal_agent_kind",
                "agent-kind-contradiction",
                "clause uses the word 'agent' but the record says the fact involves none",
            )
        )
    elif kind == AgentKind.AI and not uses_agent_word:
        out.append(
            Finding(
                "warn",
                "signal_agent_kind",
                "agent-kind-unused",
                "record classifies an AI-agent fact but the clause never says so",
            )
        )

    return out


# --- file-level audit ----------------------------------------------------


@dataclass
class RecordAudit:
    rows: int = 0
    checked: int = 0
    errors: list[str] = None  # type: ignore[assignment]
    warnings: list[str] = None  # type: ignore[assignment]
    missing_columns: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.errors = self.errors or []
        self.warnings = self.warnings or []
        self.missing_columns = self.missing_columns or []

    @property
    def failed(self) -> bool:
        return bool(self.errors) or bool(self.missing_columns)


def audit_records(
    rows: list[dict],
    fieldnames: list[str] | tuple[str, ...] | None = None,
    as_of: datetime.date | None = None,
) -> RecordAudit:
    """Audit a whole list's research records.

    When the header predates the record, this returns exactly one file-level finding
    and checks nothing per row — see the module docstring. Re-running research is the
    only fix, and printing it 496 times does not make that clearer.
    """
    names = list(fieldnames) if fieldnames is not None else (list(rows[0]) if rows else [])
    a = RecordAudit(rows=len(rows))
    missing = missing_record_columns(names)
    if missing:
        a.missing_columns = missing
        return a
    for r in rows:
        a.checked += 1
        who = (r.get("email") or r.get("company") or "?").strip()
        for f in check_record(r, as_of=as_of):
            line = f"{f.rule}: {who} — {f.detail}"
            (a.errors if f.level == "block" else a.warnings).append(line)
    return a
