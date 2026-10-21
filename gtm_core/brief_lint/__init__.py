"""Gate the market-intelligence brief on whether a human outside the build can read it.

The 2026-07-29 brief shipped with `enterprise_filings` in a table, "neither reproduces"
as a finding, "seven speakers" in the opening paragraph, and three different wrong source
counts ("ten live sources", "10 / 10 present", "11 of 15 present"). Every one of those
passed every existing gate, because no gate had an opinion about the *reader*. The brief
was written for someone who already knew the system — which is everyone who built it and
nobody who needs it.

The fix is a surface split, and this module enforces it:

    the markdown brief is the INTERNAL RECORD  — mechanics belong there
    the HTML companion is the READER SURFACE   — mechanics are a defect there

So `gtm_core.voc.evidence` may appear in the `.md` and never in the `.html`. Same facts,
two registers, one of them chosen for a product/sales/strategy reader who has no idea what
a lane is.

Fifteen rule tiers, deliberately split between what a machine can decide and what it cannot:

    T1  banned identifiers  — module paths, filenames, field names, source ids   ERROR
    T2  banned vocabulary   — our words for our machinery, with replacements     ERROR
    T3  stale counts        — every "N sources" claim, checked against the code  ERROR
    T4  opaque cross-refs   — "see §04c" with no section name attached           ERROR
    T5  unsourced figures   — a number in a reader block with no link near it    WARN
    T6  structure           — dangling anchors, duplicate ids, scripts, CDN      ERROR
    T7  density             — sentence and paragraph length, bold-per-sentence   WARN
    T8  audience routing    — every section declares its audience; operator      ERROR
                              content only in the operator appendix
    T9  takeaway-first      — a long callout must open with its takeaway;        ERROR
                              caveats may not precede findings; actions are
                              split by team (Marketing / Sales / Product)
    T10 render integrity    — markup that does not render as written: a class     ERROR
                              with no CSS rule, or an inline element whose
                              vertical margin is silently dropped
    T11 internal consistency— the same claim counted two ways in two tables      ERROR
                              (exact); a sentence asserting what another          WARN
                              section contains (re-verify by hand)
    T12 template residue    — an unreplaced {{placeholder}}, the template's own    ERROR
                              build instructions, or a comment describing a
                              placeholder that has since been replaced
    T13 component contract  — a styled component written without the child its     ERROR
                              own CSS targets (a bar track with no fill)
    T14 evidence linkage    — a table row in §02/04b/04c/04d with no citation      ERROR
                              out and no speaker chip
    T15 required sections   — the reader surface is missing a section              ERROR
                              html-companion.md's structure table defines

T8 exists because the 2026-07-29 brief interleaved operator notes — registry-tier
decisions, a blocked follow-up, a fact-check of an internal newsletter — with reader
content, and the reader (correctly) asked why any of it was there. Sections carry
`data-audience="all|marketing|sales|product|operator"`; operator-tagged sections must
follow every reader-facing one, and INSIDE an operator section the reader-surface tiers
(T1/T2/T5/T7) do not apply — the operator appendix is the one place mechanics are allowed
on the reader surface, which is precisely what makes banning them everywhere else fair.

T9 encodes the reviewed failure mode of the callouts: leading with method ("65 tasks, 824
criteria…") instead of meaning. A callout over 60 words must open with a bold takeaway
sentence; a "before quoting" caveat block may not appear before the first findings
section; and the actions section must be split by team, because "what does this mean for
me" was the question every reader block failed to answer.

T10 exists because the 2026-08-11 brief shipped twice with markup naming classes its own
stylesheet never defined. A whole section rendered as unstyled run-on text, and every
number in a table collided with its sub-label — "<b>8</b><span class=sub>8 companies</span>"
rendered as "88 companies", which the reader parsed as eighty-eight. Both times a human
reading the published page was the first thing to notice, and the source looks correct:
the markup IS separated, it is the inline box that drops the margin. Statically decidable,
so there is no excuse for a person finding it first.

T12 and T13 both exist because the 2026-08-18 brief shipped with defects a reader saw
first. T12: the browser tab read "{{Voice of the Customer}}" because replacing the body
leaves the <head> untouched, and the template's own build instructions were still the first
22 lines of the file. T13: a bar chart rendered as five empty outlines, because ".dseg" is
the track and the coloured fill is an inner "<i>" the author never wrote — every class was
defined and spelled correctly, so T10 passed. T13's contract is derived from the stylesheet
(".cls tag" implies an element with "cls" contains a "<tag>"), never transcribed, so a new
component gets checked the moment its CSS lands.

T14 and T15 exist because the 2026-09-01 brief shipped with real evidence (an Aurora
ransomware campaign, verbatim Syften developer quotes) grounding some claims and none
grounding others in the same four sections, and with four of twelve reader sections and
three of five appendices missing outright while the paired `.md` record had every one of
them — a rewrite that dropped sections rather than compressing them, past a linter that had
no opinion on completeness. T14 checks the sections that exist; T15 checks that they exist
in the first place — neither substitutes for the other. Both key sections off the heading
text in html-companion.md's structure table, not the anchor id: an id is not a stable
convention across issues (the same brief used `id="s3"` for "What people say about us",
which the template file itself uses for "BD focus").

T1's identifier list and T3's expected counts are DERIVED from the collector and the
watermark policies at run time, never transcribed here. That is the point: add a
seventeenth source and this linter learns its id and its human-readable name in the same
commit, instead of going quietly out of date the way the counts did.

What this cannot do, stated plainly so nobody mistakes a green run for a readable brief:
it cannot tell you whether an executive understands a sentence. "Add the 36.2% benchmark
with its method line" passes all seven tiers and is still unreadable. T5 and T7 are the
closest approximation and they are warnings on purpose.

Usage:
    python -m gtm_core.brief_lint <path...>            # surface inferred from suffix
    python -m gtm_core.brief_lint --strict <path...>    # warnings become failures
    python -m gtm_core.brief_lint --surface reader ...  # force the stricter surface
    python -m gtm_core.brief_lint --json <path...>      # machine-readable findings

Exit 0 clean, 1 on any ERROR (or any finding under --strict).

Escape hatch, for the case the rules get wrong rather than the prose: put
`<!-- lint-ok T2: quoting a customer verbatim -->` on the offending line or the line
above it. It must name the tier and give a reason — a bare marker is rejected, because an
unexplained suppression is how a gate becomes decorative.
"""

from __future__ import annotations

from .api import lint, report  # noqa: F401
from .cli import main  # noqa: F401

# Eager, complete re-export of the pre-split module surface (PRD §5 rule 1):
# every submodule is imported here, so module-level registrations run on
# `import <package>` exactly as they did on `import <module>`.
from .model import ERROR, READER, RECORD, ROOT, WARN, Finding  # noqa: F401
from .parse import (  # noqa: F401
    _BLOCKQUOTE_CLOSE,
    _BLOCKQUOTE_OPEN,
    _LINT_OK_RE,
    _TAG_RE,
    _blocks,
    _element_inner,
    _flatten,
    _is_mostly_quotation,
    _leaf_divs,
    _line_index,
    _quoted_lines,
    _strip_tags,
    _suppressions,
    expected_counts,
    infer_surface,
    source_names,
)
from .rules_audience import (  # noqa: F401
    _in_ranges,
    _operator_ranges,
    t8_audience,
    t9_takeaway_first,
)
from .rules_evidence import (  # noqa: F401
    _claim_rows,
    t3_counts,
    t5_unsourced_figures,
    t11_internal_consistency,
    t14_evidence_linkage,
)
from .rules_language import t1_identifiers, t2_vocabulary, t4_opaque_refs  # noqa: F401
from .rules_render import t7_density, t10_render_integrity, t13_component_contract  # noqa: F401
from .rules_structure import t6_structure, t12_template_residue, t15_required_sections  # noqa: F401
from .sections import (  # noqa: F401
    _H2_RE,
    _SECTION_RE,
    _T14_CITED_SECTIONS,
    REQUIRED_READER_APPENDICES,
    REQUIRED_READER_SECTIONS,
    _html_sections,
)
from .vocab import (  # noqa: F401
    AMBIGUOUS_NOUNS,
    AUDIENCES,
    COUNTED_NOUNS,
    IDENTIFIER_RULES,
    NUMBER_WORDS,
    QUALIFIER_AFTER,
    QUALIFIER_BEFORE,
    REF_STOPWORDS,
    VENDOR_TOOLS,
    VOCABULARY,
)

__all__ = [
    "ROOT",
    "ERROR",
    "WARN",
    "READER",
    "RECORD",
    "Finding",
    "source_names",
    "expected_counts",
    "IDENTIFIER_RULES",
    "VOCABULARY",
    "VENDOR_TOOLS",
    "REF_STOPWORDS",
    "NUMBER_WORDS",
    "COUNTED_NOUNS",
    "QUALIFIER_BEFORE",
    "QUALIFIER_AFTER",
    "AMBIGUOUS_NOUNS",
    "t1_identifiers",
    "t2_vocabulary",
    "t3_counts",
    "t4_opaque_refs",
    "t5_unsourced_figures",
    "AUDIENCES",
    "t8_audience",
    "t9_takeaway_first",
    "t6_structure",
    "t11_internal_consistency",
    "t10_render_integrity",
    "t12_template_residue",
    "t13_component_contract",
    "t7_density",
    "infer_surface",
    "REQUIRED_READER_SECTIONS",
    "REQUIRED_READER_APPENDICES",
    "t14_evidence_linkage",
    "t15_required_sections",
    "lint",
    "report",
    "main",
]
