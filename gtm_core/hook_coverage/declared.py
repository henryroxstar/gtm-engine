from __future__ import annotations

import re
from dataclasses import dataclass

from .matrix import Matrix, UnknownHookCell, _clean_cell

# --- what a spec declares -----------------------------------------------------------

# Two surfaces, one field — the same split `_CAPABILITY_RE` already carries below. A sequence
# spec writes `hook_cell:` in a fenced front block; a 1:1 pack writes `**Hook cell:**` in a
# markdown header. Until 2026-09-04 only the first form was read, so a pack could declare a cell
# and the audit would see nothing and pass — the declaration was decorative.
#
# The value is taken WHOLE. A first version stripped a trailing parenthetical, meaning to drop a
# "(Builder grid)" annotation, and silently truncated the real matrix signal
# "Compliance event (audit, breach)" — a signal name may contain parentheses and nothing tells
# the two apart. Annotations belong outside the field, not in a regex's guesswork.
_HOOK_CELL_RE = re.compile(
    r"^\**hook[_ ]cell\**:\**\s*(?P<value>.+?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_ARGUMENT_ID_RE = re.compile(r"^argument_id:\s*(?P<value>.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_PREMISE_RE = re.compile(r"^premise:\s*(?P<value>.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_STAKES_RE = re.compile(r"^stakes:\s*(?P<value>.+?)\s*$", re.MULTILINE | re.IGNORECASE)
# Sequence specs declare `capability: identity` in a fenced front block; 1:1 outreach
# packs declare `**Capability:** identity` in a markdown header. One field, two
# surfaces, one parser -- a second regex is how the two drift apart.
_CAPABILITY_RE = re.compile(
    r"^\**capability\**:\**\s*(?P<value>.+?)\s*$", re.MULTILINE | re.IGNORECASE
)


@dataclass(frozen=True)
class DeclaredCell:
    persona: str
    signal: str
    argument_id: str = ""
    raw: str = ""


def declared_cell(spec_text: str, matrix: Matrix | None = None) -> DeclaredCell | None:
    """The matrix cell a spec claims to implement, or None if it declares none.

    Read with one regex per field over the spec's fenced ``Key: value`` front block —
    the same single-line read ``merge_render_linter`` already uses for ``Sign-off``,
    so the field costs no new parser.

    Pass ``matrix`` to *validate* the declaration: an unknown persona or signal raises
    :class:`UnknownHookCell`, because a spec free to invent its own coordinates can
    declare conformance to a cell that does not exist. Called without a matrix this is
    a pure read, which is what lets the H0 baseline report "0 of 4 specs declare a
    cell" instead of crashing on the four specs that predate the field.
    """
    m = _HOOK_CELL_RE.search(spec_text or "")
    if not m:
        return None
    raw = m.group("value").strip()
    persona, sep, signal = raw.partition("×") if "×" in raw else raw.partition(" x ")
    if not sep:
        persona, signal = raw, ""
    persona, signal = _clean_cell(persona), _clean_cell(signal)
    am = _ARGUMENT_ID_RE.search(spec_text or "")
    declared = DeclaredCell(
        persona=persona,
        signal=signal,
        argument_id=(am.group("value").strip() if am else ""),
        raw=raw,
    )
    if matrix is not None and matrix.ok and matrix.find(persona, signal) is None:
        raise UnknownHookCell(
            f"hook_cell {raw!r} is not a cell in the matrix "
            f"({len(matrix.cells)} cells across {len(matrix.personas)} personas)"
        )
    return declared
