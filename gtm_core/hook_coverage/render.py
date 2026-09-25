from __future__ import annotations

from collections import Counter

from .config import EXEMPLARS, JACCARD_MAX, MAX_NGRAM_EMAILS, MAX_SPECS_PER_CAPABILITY, NGRAM_N
from .coverage import Coverage
from .matrix import RowAxis


def render(cov: Coverage) -> str:
    m = cov.matrix
    # The word for a matrix ROW, taken from the matrix rather than assumed: a generated
    # matrix's rows are seats, and a line that says "unresolved persona" while counting
    # unresolved seats is a number whose denominator the reader cannot see (RowAxis).
    axis = m.row_axis if m is not None else RowAxis.PERSONA
    lines = [
        f"hook coverage — {cov.campaign or cov.profile} "
        f"· {cov.specs} spec(s) · {cov.rows} live recipient(s)",
        "",
    ]
    if m is not None:
        if m.ok:
            lines.append(
                f"  matrix: {len(m.cells)} cell(s) · shape {m.shape} · "
                f"{len(m.personas)} {m.row_axis}(s) x {len(m.signals)} signal(s) "
                f"across {len(m.segments)} segment(s)"
            )
        else:
            lines.append(f"  matrix: UNSUPPORTED ({m.shape}) — {m.reason}")
    lines.append(f"  declared cells: {cov.declared_count}/{cov.specs}")
    for name in sorted(cov.declared):
        d = cov.declared[name]
        shown = f"{d.persona} x {d.signal}" if d else "— none declared"
        lines.append(f"    {name[:52]:<54} {shown}")
    if cov.seats:
        lines.append("  seat (copy axis — what persona-lead-mismatch lints against):")
        for k, n in cov.seats.most_common():
            pct = f"{n / cov.rows:.0%}" if cov.rows else "-"
            lines.append(f"    {k:<16} {n:>5}  {pct}")
    if cov.personas or cov.unresolved:
        lines.append("  persona (message axis — what a hook_cell is chosen against):")
        for k, n in cov.personas.most_common():
            pct = f"{n / cov.rows:.0%}" if cov.rows else "-"
            lines.append(f"    {k:<16} {n:>5}  {pct}")
        if cov.unresolved:
            pct = f"{cov.unresolved_rows / cov.rows:.0%}" if cov.rows else "-"
            lines.append(
                f"    {'unresolved':<16} {cov.unresolved_rows:>5}  {pct}  "
                f"({len(cov.unresolved)} distinct title(s))"
            )
            for title, n in cov.unresolved.most_common(EXEMPLARS):
                lines.append(f"      e.g. {title[:60]} ({n})")
        if cov.unassignable:
            n = sum(cov.unassignable.values())
            pct = f"{n / cov.rows:.0%}" if cov.rows else "-"
            lines.append(
                f"    {'wrong-grid':<16} {n:>5}  {pct}  "
                f"({axis} known, no cell in this row's own segment)"
            )
            for reason, count in cov.unassignable.most_common(EXEMPLARS):
                lines.append(f"      e.g. {reason} ({count})")
    if cov.unassignable_rows:
        lines.append(
            f"  unassignable to any matrix cell: {cov.unassignable_rows} row(s) — "
            f"{cov.unresolved_axis_rows} unresolved {axis} + {sum(cov.unassignable.values())} "
            f"{axis} known but wrong segment grid. This is a list/matrix fact, not a "
            f"copy defect — widening the matrix or resolver is the operator's call."
        )
    if cov.overlaps:
        lines.append(f"  argument overlap (Jaccard, threshold {JACCARD_MAX:.2f}):")
        for p in cov.overlaps:
            flag = "  SAME ARGUMENT" if p.same_argument else ""
            lines.append(f"    {p.jaccard:.2f}  {p.a[:34]:<36} vs {p.b[:34]}{flag}")
    lines.append(
        f"  shared phrases (verbatim {NGRAM_N}-grams in > {MAX_NGRAM_EMAILS} specs): "
        f"{len(cov.shared)}"
    )
    for sp in cov.shared[:EXEMPLARS]:
        lines.append(f'    x{sp.count}  "{sp.phrase}"')
    lines.append(f"  distinct arguments: {cov.arguments} (target >= {cov.min_arguments})")
    if cov.capabilities:
        declared_caps = Counter(c for c in cov.capabilities.values() if c)
        blank = sum(1 for c in cov.capabilities.values() if not c)
        # Printed as a distribution, not only as violations: the number a drafter needs
        # before writing the next spec is "which groups are already taken", and a report
        # that speaks only when the cap breaks cannot answer that.
        lines.append(
            f"  capability spread (cap {MAX_SPECS_PER_CAPABILITY} spec(s) per capability × seat): "
            f"{len(declared_caps)} group(s) declared, {blank} spec(s) undeclared"
        )
        # One line per (capability, seat): the unit the cap counts since 2026-09-24, so the
        # OVER CAP flag and the finding it mirrors agree on what was counted.
        by_seat = Counter(
            (c, (cov.declared[s].persona if cov.declared.get(s) else ""))
            for s, c in cov.capabilities.items()
            if c
        )
        for (slug, seat), n in by_seat.most_common():
            flag = "  OVER CAP" if n > MAX_SPECS_PER_CAPABILITY else ""
            lines.append(f"    x{n}  {slug}{(' @ ' + seat) if seat else ''}{flag}")
    if cov.segment_fits or cov.signal_fits:
        lines.append("  list vs declared cell (is the copy aimed at these recipients?):")
        for name in sorted(set(cov.segment_fits) | set(cov.signal_fits)):
            sfit = cov.segment_fits.get(name)
            gfit = cov.signal_fits.get(name)
            seg = f"segment {sfit.share:>4.0%} {sfit.declared_segment}" if sfit else "segment    — "
            sig = f"signal {gfit.share:>4.0%}" if gfit else "signal    — "
            # A number derived from rows that STATE their signal is a different kind of
            # number from one inferred out of prose, and the table is where an operator
            # decides how much to trust it. Say which it is rather than printing both alike.
            if gfit and gfit.recorded:
                sig += f" ({gfit.recorded}/{gfit.rows} recorded)"
            flag = ""
            if sfit and sfit.failed(cov.min_segment_fit):
                flag = "  MISAIMED"
            elif gfit and gfit.failed(cov.min_signal_attestation):
                flag = "  unattested"
            lines.append(f"    {name[:44]:<46} {seg}  {sig}{flag}")
    lines.append("")
    if cov.findings:
        lines.append(f"  FAIL — {len(cov.findings)} finding(s):")
        lines.extend(f"    - {f}" for f in cov.findings)
    else:
        lines.append("  PASS — no hook-coverage findings.")
    if cov.warnings:
        lines.append(f"  {len(cov.warnings)} warning(s) (advisory, do not fail the run):")
        lines.extend(f"    - {w}" for w in cov.warnings)
    return "\n".join(lines)


def unresolved_json(cov) -> dict:
    """The FULL unresolved/unassignable picture, machine-readable.

    :func:`render` caps the unresolved-title exemplars at :data:`EXEMPLARS` (three), which is
    right for a human reading a report and wrong for the only use this counter has: harvesting
    title synonyms. Three exemplars out of a long tail is a sample, and a sample is what turns
    an evidence-driven loop back into a recalled one — an operator adds the three cues they
    were shown and the fourth title keeps failing silently.

    So the rendered text keeps its cap and this is the complete counter. Both derive from the
    same ``Coverage``; there is no second walk of the rows.

    The operating rule this output feeds: **add title cues to an existing persona, never
    create a new persona row for a title variant.** A new row splits the grid — the matrix is
    persona x signal, so a new row is a new empty line of cells across every segment and every
    recipient landing on it is unassignable until somebody writes them. A cue joins a title to
    a persona the matrix already knows how to argue to.
    """
    return {
        "rows": cov.rows,
        "unresolved_rows": cov.unresolved_rows,
        "unresolved_titles": dict(cov.unresolved.most_common()),
        "unassignable_rows": sum(cov.unassignable.values()),
        "unassignable": dict(cov.unassignable.most_common()),
        "personas": dict(cov.personas.most_common()),
        "seats": dict(cov.seats.most_common()),
        "exemplars_shown_in_text": EXEMPLARS,
    }
