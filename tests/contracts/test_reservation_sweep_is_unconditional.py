"""MM12: the crash sweep that releases a hard-killed run's reservation must not hang on a flag.

MCP ``dispatch`` reserves wallet credit at enqueue whatever ``COST_RESERVATION_ENABLED`` says, so the
sweep (``release_stale_reservations``, run from ``backend/main.py``'s ``_evict`` loop) is the backstop
for a hold whose run died with its worker. Armed only under the flag, it would be dormant exactly when
nothing else releases those holds. A behavioural test would have to boot the lifespan; this pins the
structure instead — the sweep call is in the loop and the loop never consults the flag.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TARGETS = (
    REPO / "backend" / "main.py",
    REPO / "backend" / "worker.py",
)


def _evict_node(target: Path) -> ast.AsyncFunctionDef:
    tree = ast.parse(target.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_evict":
            return node
    raise AssertionError(f"{target.relative_to(REPO)} no longer defines the _evict loop")


def _mentions(node: ast.AST) -> set[str]:
    """Every name and string literal the function actually uses — never a comment."""
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.add(n.value)
    return out


@pytest.mark.parametrize("target", TARGETS, ids=["main", "worker"])
def test_the_sweep_runs_in_the_evict_loop(target: Path):
    assert any("release_stale_reservations" in m for m in _mentions(_evict_node(target)))


@pytest.mark.parametrize("target", TARGETS, ids=["main", "worker"])
def test_the_sweep_does_not_consult_the_reservation_flag(target: Path):
    used = _mentions(_evict_node(target))
    assert "_reservation_enabled" not in used
    assert not any("COST_RESERVATION_ENABLED" in m for m in used)
