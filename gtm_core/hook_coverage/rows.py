from __future__ import annotations

from dataclasses import dataclass

from ..signal_record import SIGNAL_COLUMN
from .config import persona_of
from .fit import _norm_segment
from .matrix import Cell, Matrix, _clean_cell, persona_key_of_label


@dataclass(frozen=True)
class RowCell:
    """One row's own persona x segment x signal, and the cell it resolves to, or why not.

    ``reason`` is populated whenever ``cell`` is ``None`` -- never a bare, unexplained
    ``None`` -- because a silent miss here is indistinguishable from "this row is fine and
    just has no signal yet", which is the exact ambiguity ``HOOK_CELL_COLUMN``'s own
    docstring warns a hand-typed column can hide.
    """

    email: str
    persona_key: str | None
    segment: str
    signal_column: str
    cell: Cell | None
    reason: str = ""


def derive_row_cell(
    matrix: Matrix,
    *,
    email: str,
    title: str,
    segment: str,
    signal_column: str,
) -> RowCell:
    """Derive one row's matrix cell from its own title, segment and recorded signal.

    The row-level counterpart to :func:`declared_cell`: that reads what a SPEC claims;
    this reads what the ROW itself has. Fails closed rather than guessing -- an
    unresolved title, a blank signal, or a persona x signal pair that exists in the
    matrix but not in THIS row's segment grid all return ``cell=None`` with a reason
    naming which half failed, never a best-effort pick.
    """
    persona_key = persona_of(title)
    norm_segment = _norm_segment(segment)
    clean_signal = _clean_cell(signal_column)

    if persona_key is None:
        return RowCell(
            email,
            None,
            norm_segment,
            clean_signal,
            None,
            reason="title does not resolve to a matrix persona",
        )
    if not clean_signal:
        return RowCell(
            email,
            persona_key,
            norm_segment,
            clean_signal,
            None,
            reason=f"no {SIGNAL_COLUMN!r} recorded",
        )

    candidates = [
        c
        for c in matrix.cells.values()
        if persona_key_of_label(c.persona) == persona_key
        and _norm_segment(c.segment) == norm_segment
        and c.signal.lower() == clean_signal.lower()
    ]
    if not candidates:
        # Distinguish "this signal belongs to a DIFFERENT grid" from "this persona has no
        # such signal anywhere" -- the first is a segment mistake, the second is a typo or
        # an invented column, and the operator needs to know which to fix.
        in_other_grid = any(
            persona_key_of_label(c.persona) == persona_key
            and c.signal.lower() == clean_signal.lower()
            for c in matrix.cells.values()
        )
        reason = (
            f"{persona_key!r} x {clean_signal!r} exists but not in the {norm_segment!r} grid"
            if in_other_grid
            else f"no {norm_segment!r}-grid cell for persona {persona_key!r} x signal {clean_signal!r}"
        )
        return RowCell(email, persona_key, norm_segment, clean_signal, None, reason=reason)
    if len(candidates) > 1:
        # Should be unreachable if matrix persona labels are unique within a segment
        # (test_every_matrix_persona_label_has_a_unique_key_within_its_segment pins this) --
        # fail closed rather than silently pick one if that invariant is ever violated.
        return RowCell(
            email,
            persona_key,
            norm_segment,
            clean_signal,
            None,
            reason="ambiguous: more than one matrix cell matches this persona x signal x segment",
        )
    return RowCell(email, persona_key, norm_segment, clean_signal, candidates[0])


def classify_rows(matrix: Matrix, rows: list[dict]) -> list[RowCell]:
    """:func:`derive_row_cell` over a CSV's live (non-suppressed) rows."""
    out: list[RowCell] = []
    for r in rows:
        if (r.get("suppression") or "").strip():
            continue
        out.append(
            derive_row_cell(
                matrix,
                email=(r.get("email") or "?").strip(),
                title=r.get("title") or "",
                segment=r.get("segment") or "",
                signal_column=r.get(SIGNAL_COLUMN) or "",
            )
        )
    return out
