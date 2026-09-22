"""Deterministic, tenant-configurable scoring over partially-researched rows.

This package answers one question — *may this row be given a number at all?* — before it answers
*what number*. A row whose scoring inputs are absent gets a **category** naming the missing input,
never a low score, because "we never researched this" and "we researched this and it is weak" are
different findings and an operator cannot tell them apart once they are both an integer.

WHY THIS EXISTS
---------------
On 2026-09-21 a 1003-row enrichment run scored every account against a rubric invented from plan
prose while the tenant's maintained rubric sat unread in the profile. Three defects followed, none
of which any existing check could see:

* the ICP-fit axis was ``len(description) >= 60`` — a length test granting full fit credit;
* a **negative** research finding scored as a positive one, because the guard was
  ``"no signal" not in text`` and the research said *"agent activity not verified"*;
* company size was read off the upper bound of a range, so ``51-200`` became "enterprise".

806 of 1000 rows came out Tier A, and the mean score tracked **how many teams had listed a
company** rather than anything about the company. Every defect is the same shape: an open-ended
rule over free text, which is wrong in a direction nobody can see.

So: every granting value in this package is a **closed list**, and an unrecognised value refuses.

NOT TO BE CONFUSED WITH ``docs/purpose-scorecard.md``
----------------------------------------------------
That document owns the word "scorecard" elsewhere in this repo for something unrelated — a prose
judgment rubric a model self-fills before presenting a draft. This package scores *accounts*
against *tenant ICP data*; the two never meet.
"""

from __future__ import annotations

from .cli import main  # noqa: F401
from .evidence import ASSESSED_VALUES, Evidence, classify  # noqa: F401
from .loader import load, parse, scorecard_path  # noqa: F401
from .model import (  # noqa: F401
    COVERAGE_PROXY,
    Axis,
    Batch,
    Categorised,
    Result,
    ScoreCard,
    ScoreCardError,
    Scored,
)
from .score import score_row, score_rows  # noqa: F401

__all__ = [
    "COVERAGE_PROXY",
    "ASSESSED_VALUES",
    "Axis",
    "Batch",
    "Categorised",
    "Evidence",
    "Result",
    "ScoreCard",
    "ScoreCardError",
    "Scored",
    "classify",
    "load",
    "main",
    "parse",
    "score_row",
    "score_rows",
    "scorecard_path",
]
