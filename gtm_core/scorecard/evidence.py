"""The closed-list evidence classifier — the fix for the 35-row negative-finding defect.

THE DEFECT THIS REPLACES
-------------------------
The 2026-09-21 rubric granted "agent evidence" credit with a substring test:
``"no signal" not in text``. Real research prose never says "no signal" when it means "I looked
and found nothing" — it says things like *"agentic features not verified"* or *"AI agent programme
not verified this pass"*. None of those contain the substring the guard checked for, so the guard
returned ``True`` and granted full credit to a row whose own research said the opposite. 35 rows
carried this exact defect in the live run.

THE FIX
-------
:func:`classify` never inspects free text for what it means. It looks at one already-decided
**word** — the classification a researcher or a prior scoring pass recorded — and matches it
against a **closed, exact-string, case-sensitive** set of granting values. Anything else,
including a near-miss like ``"Present"`` or a plausible-looking ``"yes"``, is
:attr:`Evidence.NOT_ASSESSED`, which refuses: the row categorises, it does not score.

Case sensitivity is deliberate, not an oversight. Every normalisation rule (lower-casing, trimming
synonyms, mapping "yes"/"true"/"1" to a grant) is one more place an input nobody anticipated gets
coerced into a granting value — which is exactly the shape of the defect this module exists to
retire. ``"Present"`` is not the token ``"present"``; a caller that wrote it has not recorded a
classification this module recognises, and the honest answer is "not assessed," not a guess.

There is no substring test and no regex anywhere in this module. See
``tests/unit/test_scorecard_evidence.py`` for the AST check that keeps it that way.
"""

from __future__ import annotations


class Evidence:
    """What a row's agent-activity research established, as one closed word.

    ``present`` and ``industry_only`` are read from research prose *after* a human or a prior
    stage has already decided what it means — never derived by pattern-matching the prose here.
    """

    #: Company-specific agent evidence is on the row — a named deployment, a dated production
    #: signal, a direct quote about THIS account's own agents.
    PRESENT = "present"
    #: Agent activity exists in the account's sector, but nothing ties it to this specific
    #: company — e.g. the row cites an industry trend or a competitor's rollout.
    INDUSTRY_ONLY = "industry_only"
    #: The account was researched and no agent evidence was found. This is a real, useful,
    #: NEGATIVE finding — it still refuses to grant, but it must never be confused with the row
    #: not having been researched at all (that is :attr:`NOT_ASSESSED`).
    ABSENT = "absent"
    #: The value was missing, blank, unrecognised, or not one of the three words above. This is
    #: the ONLY value that means "this row cannot be scored on this axis" — a categorised row,
    #: never a score of zero. A row that reaches this because nobody looked yet and a row that
    #: reaches it because someone wrote a word this classifier doesn't recognise are, on purpose,
    #: indistinguishable from here: both mean "go find out," never "score it low."
    NOT_ASSESSED = "not_assessed"


#: The closed list of values :func:`classify` will ever grant on. Exact string match, no
#: normalisation. Anything not in this set — including a case variant of a member — refuses.
GRANTING_VALUES: frozenset[str] = frozenset(
    {Evidence.PRESENT, Evidence.INDUSTRY_ONLY, Evidence.ABSENT}
)

#: Every value :func:`classify` can return, granting or not. The instrument check in the test
#: module asserts this set is exactly {PRESENT, INDUSTRY_ONLY, ABSENT, NOT_ASSESSED} — if it ever
#: silently grew or shrank, every test that exercises "an unrecognised value refuses" could start
#: passing for the wrong reason.
ASSESSED_VALUES: frozenset[str] = GRANTING_VALUES | {Evidence.NOT_ASSESSED}


def classify(value: object) -> str:
    """Classify an already-decided agent-evidence word into one of :class:`Evidence`'s values.

    ``value`` is expected to be the exact string a researcher or a prior stage recorded —
    ``"present"``, ``"industry_only"``, or ``"absent"``. Anything else — ``None``, ``""``,
    whitespace, a case variant, a synonym, a sentence, or text carrying an injected instruction —
    returns :attr:`Evidence.NOT_ASSESSED`. This function never raises: refusal is the answer, not
    an exception a caller has to remember to catch.
    """
    if isinstance(value, str) and value in GRANTING_VALUES:
        return value
    return Evidence.NOT_ASSESSED
