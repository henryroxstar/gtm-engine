r"""What a blank or `—` matrix cell means to `parse_matrix` — the FR2 open assumption.

The 2026-09-24 PRD (§10 "Still assumed") recorded this as the assumption to knock down before
`matrix_view` exists: *"`parse_matrix` tolerates an empty / `—` cell without counting it as an
unknown hook."* `grep -n "blank\|empty" gtm_core/hook_coverage/matrix.py` found no explicit
handling, which is why it was written down rather than relied on.

It matters because FR2 makes `hook-matrix.md` a **generated** view, and the PRD requires a
missing angle to render as `—` rather than an omitted row — *"so a blank is visible"* (§4B).
If a rendered `—` parses back as a hook, then `parse(render(registry))` reports coverage the
registry does not have: every hole in the grid becomes a filled cell, and `hook_coverage` — the
instrument that answers "how many seats have an argument" — reads high by exactly the number of
holes. That is the coverage-instrument failure this repo has already paid for once.

These tests are written against **today's** parser, before any change, so the answer is a
measurement rather than a design intention.
"""

from __future__ import annotations

import pytest

from gtm_core.hook_coverage.declared import declared_cell
from gtm_core.hook_coverage.matrix import MatrixShape, UnknownHookCell, parse_matrix

# A minimal GRID-shape matrix: `_detect_shape` keys on a header cell naming both axes
# (`matrix.py`), so the first cell must mention persona and signal.
_GRID = """# Fixture matrix

## Segment One

| Signal → / Persona ↓ | Signal A | Signal B | Signal C |
|---|---|---|---|
| **CISO** | A real hook sentence. | — | |
"""


def test_the_fixture_parses_as_a_grid():
    """Guard against a vacuous pass.

    Every assertion below is of the form "this cell is absent". A fixture the parser rejected
    outright would satisfy all of them while proving nothing — which is precisely how a check
    that cannot discriminate gets written.
    """
    matrix = parse_matrix(_GRID)
    assert matrix.shape == MatrixShape.GRID
    assert matrix.find("CISO", "Signal A") is not None, "the real hook must parse"


def test_a_real_cell_is_kept():
    """The negative control for the two tests below."""
    matrix = parse_matrix(_GRID)
    cell = matrix.find("CISO", "Signal A")
    assert cell is not None
    assert cell.hook == "A real hook sentence."


@pytest.mark.parametrize("signal", ["Signal B", "Signal C"])
def test_a_placeholder_cell_holds_no_hook(signal):
    """An em-dash cell and an empty cell both mean "no angle here", not "a hook that reads —"."""
    matrix = parse_matrix(_GRID)
    assert matrix.find("CISO", signal) is None


def test_placeholder_cells_are_not_counted_as_coverage():
    """The number the coverage report is built on.

    One seat, three columns, one argument written. The matrix holds **one** cell.
    """
    assert len(parse_matrix(_GRID).cells) == 1


def test_declaring_a_placeholder_cell_is_an_unknown_hook_cell():
    """A spec may not declare conformance to a cell that holds nothing.

    The matrix is the vocabulary (`matrix.py`, `UnknownHookCell`). A spec free to point at a
    hole can declare a cell that was never written, which is indistinguishable from declaring
    nothing at all.
    """
    matrix = parse_matrix(_GRID)
    with pytest.raises(UnknownHookCell):
        declared_cell("hook_cell: CISO × Signal B", matrix)


def test_declaring_a_real_cell_is_accepted():
    """The negative control for the refusal above."""
    matrix = parse_matrix(_GRID)
    declared = declared_cell("hook_cell: CISO × Signal A", matrix)
    assert declared is not None
    assert declared.persona == "CISO"
    assert declared.signal == "Signal A"
