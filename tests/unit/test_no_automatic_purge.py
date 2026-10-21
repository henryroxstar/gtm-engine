"""A ban lives in code (§R13): no build step may purge or archive prospect data.

Product-owner decision, 2026-09-21: nothing is cleaned up automatically, ever — retention is the
operator's explicit `python -m gtm_core.retention_sweep`. The behavioural tests cannot see a
re-wired sweep that the campaign gate happens to refuse, so this one reads the source.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

GTM_CORE = Path(__file__).resolve().parents[2] / "gtm_core"
BUILD_STEPS = sorted(
    [
        *(GTM_CORE / "prospects_consolidate").rglob("*.py"),
        *(GTM_CORE / "lanes").rglob("*.py"),
        GTM_CORE / "prospects_import.py",
        GTM_CORE / "prospects_state.py",
        GTM_CORE / "prospect_status_cli.py",
    ]
)


def _names(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
    return found


def test_the_build_step_list_is_not_empty() -> None:
    assert len(BUILD_STEPS) >= 10


@pytest.mark.parametrize("path", BUILD_STEPS, ids=lambda p: str(p.relative_to(GTM_CORE)))
def test_no_build_step_reaches_the_retention_sweep(path: Path) -> None:
    names = _names(ast.parse(path.read_text(encoding="utf-8")))
    reached = {n for n in names if "retention_sweep" in n or n == "sweep_stale_pii"}
    assert not reached, f"{path.name} references {sorted(reached)} — retention is manual only"


def test_the_detector_sees_a_rewired_sweep() -> None:
    rewired = "def consolidate():\n    from gtm_core.retention_sweep import sweep_stale_pii\n"
    assert {"gtm_core.retention_sweep", "sweep_stale_pii"} <= _names(ast.parse(rewired))
