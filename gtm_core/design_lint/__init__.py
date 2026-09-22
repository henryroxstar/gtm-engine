"""Deterministic lint for a solution design — the SA chain's missing gate.

`build-deck` has `deck_lint` D1–D10 and `commercial-proposal` has a CLI witness on its
year-1 total. `solution-design` — the most consequential document this system produces —
had prose self-checks the model graded itself against, and shipped a body whose guardrails
contradicted each other twenty-four lines apart. Every rule below is prose the skills
already state, made executable.

| Tier | Checks |
|------|--------|
| SD1  | the three-tier read is present and in order |
| SD2  | every coverage dimension has a section that answers it |
| SD3  | quality requirements are stated (availability, latency, throughput, recovery) |
| SD4  | a glossary exists |
| SD6  | every row of a capability matrix says how ready it is |
| SD7  | a design-target is not written as though it ships |
| SD8  | no claim writes to something the document froze |
| SD9  | one action is not attributed to two different actors |
| SD11 | no two guardrails assert opposite obligations (`--skill` mode) |
| SD12 | a stated count matches the list it introduces |
| SD13 | every diagram ships with a walkthrough |

**Three severities, and the third one is the point.** SD1–SD4 and SD12–SD13 grade a
document's shape: `error` blocks delivery, `warn` does not but is graded and can be
suppressed with `<!-- lint-ok SDn: reason -->`. SD6–SD9 grade what the document *claims*,
and emit `advisory` — which blocks nothing, is not promoted by `--strict`, and takes no
`lint-ok`, because all four can be wrong in a way the shape rules cannot: the author knows
what shipped this week and this package knows what shipped when it was written. Detail and
the measurement behind each rule's shape: :mod:`gtm_core.design_lint.rules_claims`.

SD5 (decision records) and SD10 (joint-claim sourcing) are specified in the PRD and are
not implemented here — of the six claim rules the PRD names, these are the two whose object
this package has no structural handle on.

Usage::

    uv run python -m gtm_core.design_lint <design.md> [--strict] [--json]
    uv run python -m gtm_core.design_lint --skill plugin/skills/<name>/body_template.md
"""

from __future__ import annotations

from .api import coverage_map, lint, lint_skill, report
from .catalog import (
    CAPABILITY_CLAIM,
    CONSTRAINT_MARKERS,
    DIAGRAM_MARKERS,
    DIAGRAM_REFERENCE,
    EXEC_MARKERS,
    FORWARD_TAG,
    MATURITY_TAG,
    MATURITY_TAGS,
    NUMBER_WORDS,
    STATUS_HEADER,
    TIER1_MARKERS,
    TIER2_MARKERS,
    WALKTHROUGH_MARKERS,
    CoverageError,
    Dimension,
    dimensions,
    matches_marker,
    names_section,
    statuses,
)
from .cli import main
from .model import ADVISORY, ERROR, WARN, Finding, Section
from .parse import UnparseableDesign, document_text, parse_sections
from .rules_claims import _sd6, _sd7, _sd8, _sd9
from .rules_coverage import _sd1, _sd2, _sd3, _sd4, satisfied_by
from .rules_integrity import _sd12, _sd13
from .text import bullet_count, claim_units, plain, sentences, significant, table_rows, topic_terms

__all__ = [
    "ADVISORY",
    "CAPABILITY_CLAIM",
    "CONSTRAINT_MARKERS",
    "CoverageError",
    "DIAGRAM_MARKERS",
    "DIAGRAM_REFERENCE",
    "Dimension",
    "ERROR",
    "EXEC_MARKERS",
    "FORWARD_TAG",
    "Finding",
    "MATURITY_TAG",
    "MATURITY_TAGS",
    "NUMBER_WORDS",
    "STATUS_HEADER",
    "Section",
    "TIER1_MARKERS",
    "TIER2_MARKERS",
    "UnparseableDesign",
    "WALKTHROUGH_MARKERS",
    "WARN",
    "_sd1",
    "_sd12",
    "_sd13",
    "_sd2",
    "_sd3",
    "_sd4",
    "_sd6",
    "_sd7",
    "_sd8",
    "_sd9",
    "bullet_count",
    "claim_units",
    "coverage_map",
    "dimensions",
    "document_text",
    "lint",
    "lint_skill",
    "main",
    "matches_marker",
    "names_section",
    "parse_sections",
    "plain",
    "report",
    "satisfied_by",
    "sentences",
    "significant",
    "statuses",
    "table_rows",
    "topic_terms",
]
