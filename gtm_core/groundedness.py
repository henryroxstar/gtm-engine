"""Groundedness cascade — factual/substance checks on a rendered email body (P1.5,
PRD 2026-08-19-email-eval-calibration.md §3.0 layer L2).

**Reuses, does not rebuild, the clause-vs-evidence entailment check.** The dominant L2
concern — "does the row's own researched fact actually follow from its quoted source" —
already shipped the same day as this PRD's first draft, as
:func:`gtm_core.signal_record.evidence_supports` / :func:`gtm_core.signal_record.check_record`:
claim-level word-support + number-provenance against ``signal_evidence``, plus subject
match and agent-kind classification. Duplicating that here would be exactly the kind of
undocumented-drift this repo's own PRDs keep finding. This module wraps it as one stage of
a larger cascade and adds the two L2 checks nothing in this repo covers yet:

* :func:`case_study_numbers_traceable` — a body that cites a case-study-shaped number
  (a percentage, a count) must be able to point at that number somewhere in the profile's
  own ``case-studies.md``. Cheap and mechanical (string containment), and it exists
  because it already went wrong once: ``outreach-case-studies.txt``'s own header records
  a shipped defect where a 90% figure was cited for the wrong mechanism (MAS employment
  reference checks, cited as agent-action attribution) — this check catches the *fabricated
  number* half of that class (a number absent from the source file entirely); the *wrong
  mechanism* half (a real number, mapped to the wrong claim) is semantic and belongs to the
  LLM tier, not this heuristic.
* :func:`assertive_internals_claims` — the persona-review's class 4 ("asserted internals"),
  which fired on **every** row reviewed and is fixed today only by copy discipline, never
  by a check: a flat claim about what is true inside the RECIPIENT's own systems ("the
  honest answer at {{Company}} is X") reads as an unverifiable assertion about their
  architecture, and the fix is to predict the question rather than assert the answer.
  ``hedge-missing`` (in ``tests/linter/outreach_pack_linter.py``) only checks that SOME
  hedge phrase appears anywhere in the body — a body can satisfy it while still carrying
  an unhedged assertive claim in a different sentence, which is the gap this closes.

**Cascade discipline** (PRD §9: cheap heuristic → classifier → LLM judge on borderline,
never "is this true?" put to a model directly): :func:`groundedness_report` runs every
cheap tier and returns a verdict plus, for anything the heuristics could not resolve, the
atomic claims a caller with model access should route to the judge — this module never
calls a model itself, mirroring :mod:`gtm_core.adjudication`'s and
:mod:`gtm_core.eval_calibration`'s own "record, don't call" boundary.

Scored as a classifier, not a vibe score: a caller has :func:`groundedness_predictions`
turn a batch of reports into the same ``row_id -> bool`` shape
:func:`gtm_core.eval_calibration.confusion` already consumes, so precision/recall on this
cascade rides the identical statistical machinery the judge is validated with — no second
scoring path to keep in sync.

Stdlib-only, no I/O, no model call — same convention as every sibling module in this family.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from .signal_record import check_record

# --------------------------------------------------------------------------- tier 0: reuse

#: Re-exported so a caller of this module never needs a second import for the
#: already-shipped clause-vs-evidence tier — see module docstring.
research_record_findings = check_record


# --------------------------------------------------------------------------- tier 1: case-study numbers

# The trailing `\b` sits INSIDE each word-shaped alternative, not after the group: `%` is
# already a non-word char and self-delimiting, so a shared `\b` after the whole alternation
# never matches "90% " (non-word "%" followed by non-word space has no boundary between
# them) and silently dropped every percentage — found by this module's own test suite.
_NUMBER_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\s*(?:%|percent\b|x\b|times\b)", re.IGNORECASE)


def cited_numbers(body: str) -> list[str]:
    """Case-study-shaped numeric claims in a rendered body: a percentage or multiplier
    ("90%", "100 percent", "3x"). Deliberately narrower than :func:`signal_record.claim_numbers`
    (which catches every digit, including dates that belong to the signal clause, not a
    proof point) — this tier only cares about the proof-sentence shape."""
    return [m.group(0) for m in _NUMBER_RE.finditer(body or "")]


def case_study_numbers_traceable(body: str, case_studies_text: str) -> tuple[bool, list[str]]:
    """True (with an empty list) when every case-study-shaped number in ``body`` appears
    somewhere in ``case_studies_text`` (the profile's ``case-studies.md``, verbatim).

    Catches the *fabricated number* class mechanically: a number absent from the source
    file entirely cannot be a citation of it, whatever mechanism it claims to describe.
    Does **not** verify the number maps to the RIGHT mechanism — that is a semantic
    question ("does this number's claimed cause match its documented cause") this
    heuristic cannot answer and should not pretend to; route it to the LLM tier instead.
    """
    numbers = cited_numbers(body)
    if not numbers:
        return True, []
    haystack = case_studies_text or ""
    missing = [n for n in numbers if _bare_digits(n) not in _digits_in(haystack)]
    return not missing, missing


def _bare_digits(token: str) -> str:
    return re.sub(r"[^\d]", "", token)


def _digits_in(text: str) -> set[str]:
    return {_bare_digits(m.group(0)) for m in _NUMBER_RE.finditer(text)}


# --------------------------------------------------------------------------- tier 2: assertive internals claims

#: A flat copula assertion about what is true inside the RECIPIENT's own systems. Anchored
#: on "at/inside {{Company}}" (or a rendered company name) plus a copula, so it does not
#: fire on an assertion about US, about the market, or about the recipient's PUBLIC facts
#: (their title, their industry) — only about their internal architecture/practice, which
#: is exactly the class no outsider can actually verify without being told.
_ASSERTIVE_INTERNALS_RE = re.compile(
    r"\b(?:the (?:honest|real|likely) (?:answer|truth|reality)|the reality)\b"
    r"[^.?!]{0,40}\b(?:at|inside|within)\b[^.?!]{0,40}\bis\b",
    re.IGNORECASE,
)

#: A hedge phrase near enough to the assertion to concede it might be wrong. Deliberately
#: reuses the same vocabulary `HEDGE_CUES` in outreach_pack_linter checks for, but this
#: module stays independent of that one (stdlib-only; no cross-import needed for four
#: literal phrases) rather than importing a test-harness module from gtm_core/.
_HEDGE_NEARBY_RE = re.compile(
    r"\b(?:my (?:read|hunch|bet|guess|sense)|tell me if|correct me if|you may well have this)\b",
    re.IGNORECASE,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def assertive_internals_claims(body: str, *, hedge_window_sentences: int = 1) -> list[str]:
    """Sentences that flatly assert a fact about the recipient's own internals with no
    hedge phrase in that sentence or the one immediately before it.

    Window, not whole-body presence: a hedge phrase used once at the top of a long body
    does not retroactively soften an unhedged assertion three sentences later — the
    reader meets each sentence on its own.
    """
    sentences = [s for s in _SENTENCE_SPLIT_RE.split((body or "").strip()) if s]
    out = []
    for i, sentence in enumerate(sentences):
        if not _ASSERTIVE_INTERNALS_RE.search(sentence):
            continue
        window = sentences[max(0, i - hedge_window_sentences) : i + 1]
        if not any(_HEDGE_NEARBY_RE.search(s) for s in window):
            out.append(sentence.strip())
    return out


# --------------------------------------------------------------------------- cascade aggregation


@dataclass(frozen=True)
class GroundednessReport:
    row_id: str
    research_findings: list  # gtm_core.merge_hygiene.Finding, from tier 0
    untraceable_numbers: list[str]  # tier 1
    unhedged_internals_claims: list[str]  # tier 2
    needs_judge: bool  # True when a cheap tier found something a human/LLM must resolve

    @property
    def clean(self) -> bool:
        blocking_research = [
            f for f in self.research_findings if getattr(f, "level", "") == "block"
        ]
        return (
            not blocking_research
            and not self.untraceable_numbers
            and not self.unhedged_internals_claims
        )


def groundedness_report(
    row_id: str,
    row: dict,
    body: str,
    case_studies_text: str = "",
) -> GroundednessReport:
    """Run every cheap tier on one rendered row and return one aggregate report.

    ``row`` is the signal record (the same dict :func:`signal_record.check_record` takes —
    ``signal_clause``, ``signal_evidence``, ``company``, etc.); ``body`` is the RENDERED
    email text tier 1/2 read.
    """
    research_findings = check_record(row)
    traceable, untraceable = case_study_numbers_traceable(body, case_studies_text)
    unhedged = assertive_internals_claims(body)
    return GroundednessReport(
        row_id=row_id,
        research_findings=research_findings,
        untraceable_numbers=untraceable,
        unhedged_internals_claims=unhedged,
        needs_judge=bool(untraceable or unhedged),
    )


def groundedness_predictions(reports: Sequence[GroundednessReport]) -> dict[str, bool]:
    """``row_id -> predicted send_it`` in the exact shape
    :func:`gtm_core.eval_calibration.confusion` consumes, so the cascade is scored with
    the same TPR/TNR/kappa machinery the judge is validated with — one statistical path,
    not two to keep in sync."""
    return {r.row_id: r.clean for r in reports}


# --------------------------------------------------------------------------- tier 3: premise entailment (the judge's half)

#: Prompt sent to the ``judge`` role (registry: anthropic / claude-haiku-4-5) for the rows the
#: deterministic premise check cannot settle. Kept HERE rather than in a skill so the question
#: the judge is asked is versioned alongside the code that builds it, and so the two tiers are
#: provably asking about the same premise.
PREMISE_JUDGE_PROMPT = """\
You are checking one claim against one piece of evidence. Answer only from the evidence given.

PREMISE the email's argument requires:
{claim}

EVIDENCE recorded about this company (a verbatim span of a published source):
{evidence}

Question: does the evidence ESTABLISH the premise for this company?

Rules:
- "Establishes" means a careful reader would accept the premise on this evidence alone.
- If the premise asserts several of something ("more than one framework", "across clouds")
  and the evidence attests exactly one, the answer is NO. That is the most common case.
- Evidence that is true, specific and about the right company can still fail this. Relevance
  is not entailment.
- Do NOT use anything you know about the company beyond the evidence. If the evidence does
  not say it, it is not established.
- Default to NO when uncertain.

Return JSON: {{"entailed": true|false, "why": "<one sentence quoting the deciding words>"}}
"""


@dataclass(frozen=True)
class PremiseCheck:
    """One row's premise verdict, from whichever tier settled it."""

    email: str
    premise: str
    #: "deterministic" when term arity settled it, "judge" when it was escalated.
    tier: str
    entailed: bool
    detail: str = ""


def premise_cascade(
    rows: Sequence[dict],
    premise,
    *,
    evidence_fields: tuple[str, ...] = ("signal_evidence", "signal_clause", "why_now"),
) -> tuple[list[PremiseCheck], list[dict]]:
    """Split rows into ones the cheap tier settles and ones that need the judge.

    Returns ``(settled, escalate)`` where ``escalate`` is a list of
    ``{"email", "claim", "evidence", "prompt"}`` dicts ready to send to the ``judge`` role.

    **This module never calls a model** — the same boundary
    :mod:`gtm_core.adjudication` holds ("record, don't call"): a skill runs the prompts and
    feeds the answers back, so every gate here stays deterministic, offline and testable, and
    the §R2 budget check happens at the call site that spends the money.

    The cascade, cheap stage first (PRD 2026-08-19 §3.0's production shape):

    * a row attesting **more** than ``min_distinct`` terms is entailed, settled, no spend;
    * a row attesting **zero** terms is not entailed, settled, no spend — nothing in its
      evidence is even on the topic, so there is nothing for a judge to weigh;
    * a row in between — it attests something but not enough — is exactly the ambiguous case
      the term list cannot adjudicate ("Bedrock AgentCore" may or may not imply a second
      framework), and only those are escalated. On the 2026-08-21 lists that is a small
      minority of rows, which is what keeps the judge affordable per-row rather than sampled.
    """
    settled: list[PremiseCheck] = []
    escalate: list[dict] = []
    for r in rows:
        email = (r.get("email") or "?").strip()
        evidence = " ".join(str(r.get(f) or "") for f in evidence_fields).strip()
        # `hits_for` is the one matcher every premise reader shares (2026-09-24): it strips
        # the account's own name and reads `industry_terms` off the row's industry field,
        # so this cascade settles the same rows the resolver and the render gate settle.
        hits = premise.hits_for(r, evidence_fields)
        if len(hits) >= premise.min_distinct:
            settled.append(
                PremiseCheck(email, premise.key, "deterministic", True, f"attests {sorted(hits)}")
            )
        elif not hits:
            settled.append(
                PremiseCheck(
                    email,
                    premise.key,
                    "deterministic",
                    False,
                    "evidence carries none of the premise's terms",
                )
            )
        else:
            escalate.append(
                {
                    "email": email,
                    "claim": premise.claim or premise.key,
                    "evidence": evidence,
                    "prompt": PREMISE_JUDGE_PROMPT.format(
                        claim=premise.claim or premise.key, evidence=evidence
                    ),
                }
            )
    return settled, escalate
