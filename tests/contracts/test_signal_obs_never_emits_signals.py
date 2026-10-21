"""Contract: an observation is not a signal, and this package can never become one.

``gtm_core.signals`` turns a detected event into a row in ``history.jsonl`` that
``agent/signal_dispatch.py`` can start a run from. Observations are read-only evidence for a
view; if ``signal_obs`` (or the later ``signal_view``) ever imported the signal builder or the
history appender, a public member list could start a run by itself. Enforced on the syntax tree,
and the checker is proven able to fail (a control source that breaks each rule).

NAMING TRAP: ``gtm_core/voc/signals.py`` is a different module and is not what this pins.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_TARGETS = [REPO / "gtm_core" / "signal_obs", REPO / "gtm_core" / "signal_view.py"]
_BANNED_MODULES = {"gtm_core.signals", "gtm_core.ledgers", "agent.ledgers"}
_BANNED_NAMES = {"append_history", "record_signals", "build_signal", "new_signals"}


def _files() -> list[Path]:
    out: list[Path] = []
    for t in _TARGETS:
        if t.is_dir():
            out += sorted(t.rglob("*.py"))
        elif t.is_file():
            out.append(t)
    return out


def _absolute(module: str | None, level: int, package: str) -> str:
    if level == 0:
        return module or ""
    base = package.split(".")
    base = base[: len(base) - (level - 1)] if level > 1 else base
    return ".".join([*base, *([module] if module else [])])


def violations(source: str, package: str = "gtm_core.signal_obs") -> list[str]:
    found: list[str] = []
    tree = ast.parse(source)
    docstrings = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and n.body
        and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Constant)
    }
    for node in ast.walk(tree):
        if id(node) in docstrings:
            continue  # prose may name the file; only a string the code uses matters
        if isinstance(node, ast.Import):
            found += [f"imports {a.name}" for a in node.names if a.name in _BANNED_MODULES]
        elif isinstance(node, ast.ImportFrom):
            mod = _absolute(node.module, node.level, package)
            if mod in _BANNED_MODULES:
                found.append(f"imports from {mod}")
            found += [
                f"imports {mod}.{a.name}"
                for a in node.names
                if f"{mod}.{a.name}" in _BANNED_MODULES
            ]
            found += [f"imports {a.name}" for a in node.names if a.name in _BANNED_NAMES]
        elif isinstance(node, ast.Attribute) and node.attr in _BANNED_NAMES:
            found.append(f"uses .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in _BANNED_NAMES:
            found.append(f"uses {node.id}")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "history.jsonl" in node.value
        ):
            found.append("names history.jsonl")
    return found


def test_there_is_something_to_check():
    assert any(p.name == "extract.py" for p in _files())


@pytest.mark.parametrize("path", _files(), ids=lambda p: str(p.relative_to(REPO)))
def test_signal_obs_never_reaches_the_signal_feed(path):
    assert violations(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "source",
    [
        "import gtm_core.signals",
        "from gtm_core.signals import record_signals",
        "from gtm_core import signals",
        "from .. import signals",
        "from ..signals import build_signal",
        "from gtm_core.ledgers import Ledgers",
        "def f(l):\n    l.append_history({})",
        "P = 'content/x/history.jsonl'",
    ],
)
def test_the_checker_fails_on_each_way_to_break_the_rule(source):
    assert violations(source), source
