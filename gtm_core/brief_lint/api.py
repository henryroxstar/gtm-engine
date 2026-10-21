from __future__ import annotations

from pathlib import Path

from .model import ERROR, READER, Finding
from .parse import (
    _flatten,
    _line_index,
    _quoted_lines,
    _suppressions,
    expected_counts,
    source_names,
)
from .rules_audience import _operator_ranges, t8_audience, t9_takeaway_first
from .rules_evidence import (
    t3_counts,
    t5_unsourced_figures,
    t11_internal_consistency,
    t14_evidence_linkage,
)
from .rules_language import t1_identifiers, t2_vocabulary, t4_opaque_refs
from .rules_render import t7_density, t10_render_integrity, t13_component_contract
from .rules_structure import t6_structure, t12_template_residue, t15_required_sections


def lint(text: str, surface: str) -> list[Finding]:
    """All applicable tiers for one document."""
    lines = text.splitlines()
    suppressed = _suppressions(lines)
    flat = _flatten(text)
    line_of = _line_index(text)
    findings: list[Finding] = []

    # Both surfaces: a wrong count and a pointer to nowhere are defects in the record too.
    findings += t3_counts(flat, line_of, expected_counts())
    findings += t4_opaque_refs(flat, line_of)

    if surface == READER:
        # The operator appendix is the one place mechanics are allowed on the reader
        # surface — reader-register tiers skip it; structural tiers still apply.
        op_lines: set[int] = set()
        for start, end in _operator_ranges(text):
            op_lines.update(range(line_of(start), line_of(max(start, end - 1)) + 1))
        reader_findings = (
            t1_identifiers(text, flat, line_of, source_names())
            + t2_vocabulary(flat, line_of, _quoted_lines(lines))
            + t5_unsourced_figures(text, flat, line_of)
            + t7_density(text)
        )
        findings += [f for f in reader_findings if f.line not in op_lines]
        findings += t6_structure(text)
        findings += t8_audience(text, line_of)
        findings += t9_takeaway_first(text, line_of)
        # Render integrity is NOT filtered by op_lines: markup that does not render is a
        # defect in the operator appendix exactly as much as on the reader surface.
        findings += t10_render_integrity(text, line_of)
        # Scaffolding and broken component contracts are defects everywhere in the file,
        # including the operator appendix and the <head>, so they are not op_lines-filtered.
        findings += t12_template_residue(text, line_of)
        findings += t13_component_contract(text, line_of)
        # Same reasoning as T10: a brief that contradicts itself is wrong everywhere, so the
        # operator appendix is not exempt.
        findings += t11_internal_consistency(text, line_of)
        # The four cited sections (02/04b/04c/04d) are never operator content, so this is
        # not op_lines-filtered either.
        findings += t14_evidence_linkage(text, flat, line_of)
        # A missing section is a document-level defect, not tied to any one line.
        findings += t15_required_sections(text)

    return [f for f in findings if f.tier not in suppressed.get(f.line, set())]


def report(path: Path, findings: list[Finding], strict: bool) -> None:
    """Group by tier, worst first, with the fix on every line."""
    order = [
        "T1",
        "T2",
        "T3",
        "T4",
        "T6",
        "T8",
        "T9",
        "T10",
        "T11",
        "T12",
        "T13",
        "T14",
        "T15",
        "T5",
        "T7",
    ]
    titles = {
        "T1": "internal identifiers on the reader surface",
        "T2": "house jargon a reader outside the build will not know",
        "T3": "counts that no longer match the code",
        "T4": "section references that do not say what they point at",
        "T5": "figures with no source a reader can follow",
        "T6": "structural defects",
        "T7": "density (advisory)",
        "T8": "audience routing",
        "T9": "takeaway-first",
        "T10": "render integrity — markup that does not render as written",
        "T11": "internal consistency — the brief disagreeing with itself after an edit",
        "T12": "template residue — the template's own scaffolding still in the file",
        "T13": "component contract — a styled component missing the child its CSS targets",
        "T14": "evidence linkage — a table row in §02/04b/04c/04d with no citation",
        "T15": "required sections — a section html-companion.md defines is missing outright",
    }
    print(f"\n{path}")
    for tier in order:
        rows = [f for f in findings if f.tier == tier]
        if not rows:
            continue
        gate = "FAIL" if (rows[0].severity == ERROR or strict) else "warn"
        print(f"\n  [{tier}] {titles[tier]} — {len(rows)} ({gate})")
        for finding in rows[:15]:
            where = f":{finding.line}" if finding.line else ""
            print(f"    {finding.rule}{where}: “{finding.excerpt}” → {finding.fix}")
        if len(rows) > 15:
            print(f"    ... and {len(rows) - 15} more")
