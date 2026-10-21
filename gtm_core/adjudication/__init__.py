"""Adjudication — the reading pass, as a ranker rather than a gate.

Forty-three deterministic rules guard this pipeline. Every defect class they encode
was originally found the same way: a human read the emails and noticed something. The
rules then made that class **non-recurring**. None of them has ever found a *new* one,
and none of them can: a regex knows the shapes it was given.

So the arrangement in force through August 2026 had it backwards — generation by a
model that can read, verification by patterns that cannot. Three review rounds each
surfaced novel classes the gates had no opinion about (proof mapped to the wrong
mechanism, an offer whose tone read as cocky, a seat problem the recipient does not
actually have), and each round ended by encoding those classes as more regex, which
guaranteed a fourth round would find different ones. An aesthetic loop with no
measurement has no fixed point.

This module does not add a forty-fourth rule. It makes the reading pass a *first-class
step with the properties the gates already have* — deterministic sampling, a recorded
verdict per item, and an explicit statement of what was NOT read:

* :func:`stratify` / :func:`sample` — a covering sample across the axes that actually
  vary the copy (seat, signal-vs-generic, tier), chosen by a stable hash so the same
  list yields the same sample on every run and two reviewers can be compared. Reading
  the first fifty rows of a file sorted by company name is not a sample of anything.
* :func:`coverage` — which strata the read touched and which it did not. "I reviewed
  50 emails" and "the list is reviewed" are different claims; without this, the first
  gets reported as the second.
* :func:`rank` — send order. The reading produces a score, the score picks which 30 go
  out first, and shipping 30 with an outcome attached is worth more than a fourth
  review round.
* :func:`novel_classes` — defect classes the reader named that no existing rule covers.
  These, and only these, are the candidates for becoming rule forty-four. A class the
  rules already cover recurring in a read means a gate is inert, which is a different
  and more urgent bug.

**This is not a gate and must not become one.** Its verdicts rank and record; they
never block, because a blocking check whose author is a language model is a check that
can be argued with, and the whole value of the deterministic tier is that it cannot.
The two tiers do different jobs: rules prevent recurrence, reading finds what recurs.

Stdlib-only, no I/O beyond JSONL read/write on paths the caller supplies.
"""

from __future__ import annotations

from .cli import _known_from, main  # noqa: F401
from .completeness import Completeness, completeness, covered_classes  # noqa: F401
from .defects import DEFECT_SCOPE, defect_scope, normalize_defect_class  # noqa: F401
from .disposal import TARGETING_DEFECTS, Disposal, disposal_audit  # noqa: F401
from .io import _read_fieldnames, _read_rows, read_records, write_records  # noqa: F401

# Eager, complete re-export of the pre-split module surface (PRD §5 rule 1):
# every submodule is imported here, so module-level registrations run on
# `import <package>` exactly as they did on `import <module>`.
from .model import DEFAULT_STRATA, Adjudication  # noqa: F401
from .repair import REPAIR_ATTEMPT_CAP, RepairCandidate, repair_queue  # noqa: F401
from .report import (  # noqa: F401
    REGENERATION_CAP,
    defect_report,
    regeneration_count,
    require_qa,
    source_name,
    spec_diff,
)
from .sampling import (  # noqa: F401
    Coverage,
    _stable_key,
    collapsed_axes,
    coverage,
    sample,
    stratify,
    stratum_of,
)
from .stability import _unit, unstable_bodies, unstable_row_id  # noqa: F401
from .tally import Tally, TallyRow, collapse_touches, send_set, tally, write_tally  # noqa: F401
from .verdicts import (  # noqa: F401
    _VERDICT_RANK,
    _VERDICT_SEVERITY,
    novel_classes,
    rank,
    worst_verdict,
)

__all__ = [
    "DEFAULT_STRATA",
    "Adjudication",
    "stratum_of",
    "stratify",
    "collapsed_axes",
    "sample",
    "coverage",
    "rank",
    "novel_classes",
    "read_records",
    "write_records",
    "repair_queue",
    "REPAIR_ATTEMPT_CAP",
    "REGENERATION_CAP",
    "defect_report",
    "spec_diff",
    "regeneration_count",
    "require_qa",
    "source_name",
    "completeness",
    "covered_classes",
    "worst_verdict",
    "Disposal",
    "disposal_audit",
    "TARGETING_DEFECTS",
    "DEFECT_SCOPE",
    "defect_scope",
    "normalize_defect_class",
    "TallyRow",
    "Tally",
    "unstable_row_id",
    "unstable_bodies",
    "tally",
    "collapse_touches",
    "send_set",
    "write_tally",
    "main",
]
