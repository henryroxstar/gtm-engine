"""T0-guard: the modules the one-product work must not move stay byte-identical to BASE.

Cell ids, the enrolment gate, the publish path, signal dispatch and the merge are the surfaces whose
behaviour every historical row depends on. The PRD keeps them out of scope on purpose, so a diff in
one of them is a scope breach, not a fix. ``send_cards.py`` is deliberately absent: another session
has uncommitted edits in it.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PINS = json.loads((Path(__file__).parent / "goldens" / "one_product_untouched.json").read_text())[
    "pins"
]


def _digest(spec: str) -> str:
    path, _, func = spec.partition("::")
    data = (REPO / path).read_bytes()
    if not func:
        return hashlib.sha256(data).hexdigest()
    src = data.decode("utf-8")
    node = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == func)
    return hashlib.sha256(ast.get_source_segment(src, node).encode()).hexdigest()


@pytest.mark.parametrize("spec", sorted(PINS))
def test_untouched_at_base(spec):
    assert _digest(spec) == PINS[spec], f"{spec} changed — the PRD lists it as out of scope"
