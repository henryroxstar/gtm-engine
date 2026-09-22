"""``icp keyword`` — the documented 2026-09-04 methodology as a command.

The methodology, from ``profiles/<t>/knowledge/icp-scoring.toml``'s own comment: candidate
phrase -> regex-test it against the live backlog -> if the hit count is small and every hit is a
real match for the cohort, add it; if it's large or pulls in the wrong shape, reject it and
record why. This module is exactly that regex-test, and nothing more — it calls
:func:`gtm_core.prospects_backlog._cohort_pattern`, the SAME function
:func:`gtm_core.prospects_backlog.score_account` uses to rank the enrichment queue. A second,
independently-written matcher here would drift from the selector silently, which is worse than
no critique at all (a critique that disagrees with the selector is worse than none).

**Field defaults to ``description``, not ``industry``.** Re-derived against a live tenant
backlog: every one of the four phrases a tenant rubric documents as hand-tested is a hit against
``description`` and scores zero against ``industry``,
because :func:`gtm_core.prospects_backlog.score_account` matches a cohort's ``keywords`` against
``industry`` only and its ``description_keywords`` against ``description`` only. A phrase proven
against the wrong field is a phrase added to the wrong rubric key, where it will match nothing at
selection time — so this module reports which rubric key (``keywords`` vs ``description_keywords``)
the phrase's field implies, and the CLI prints it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import prospects_backlog as pb

#: The two fields a keyword can be tested against, and the rubric key each implies —
#: score_account.py:251-260 keeps these strictly separate (the 2026-08-11 `financ` incident:
#: free-text description matched "financial year" and misrouted 227/500 accounts).
_FIELD_TO_RUBRIC_KEY = {
    "description": "description_keywords",
    "industry": "keywords",
}

DEFAULT_FIELD = "description"
DEFAULT_MAX_HITS = 10
DEFAULT_SAMPLE = 5


@dataclass(frozen=True)
class KeywordHits:
    """One candidate phrase's hit count against the live backlog, plus a bounded sample to read."""

    phrase: str
    field: str
    rubric_key: str
    count: int
    matches: list[dict] = field(default_factory=list)

    def sample(self, n: int = DEFAULT_SAMPLE) -> list[dict]:
        return self.matches[:n]


def keyword_hits(
    phrase: str,
    accounts: list[dict],
    *,
    field: str = DEFAULT_FIELD,
) -> KeywordHits:
    """Hit-count ``phrase`` against ``accounts`` using the selector's own matcher.

    Builds a one-keyword synthetic cohort and calls :func:`prospects_backlog._cohort_pattern` on
    it — the same function, the same regex shape (``\\b<kw>\\w*``), never a re-derived one.
    """
    if field not in _FIELD_TO_RUBRIC_KEY:
        raise ValueError(f"unknown field {field!r}; expected one of {sorted(_FIELD_TO_RUBRIC_KEY)}")
    rubric_key = _FIELD_TO_RUBRIC_KEY[field]

    synthetic_cohort: dict = {rubric_key: [phrase]}
    pattern = pb._cohort_pattern(synthetic_cohort, rubric_key)

    matches = []
    if pattern is not None:
        for rec in accounts:
            blob = str(rec.get(field, "")).lower()
            if pattern.search(blob):
                matches.append(rec)

    return KeywordHits(
        phrase=phrase,
        field=field,
        rubric_key=rubric_key,
        count=len(matches),
        matches=matches,
    )
