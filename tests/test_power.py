"""Tests for gtm_core.power — the stdlib-only statistics leaf.

The point of the module is what it *does not* import. ``gtm_core.cells`` mutates
``sys.path`` at import time to reach the seat resolver that lives beside the linter
enforcing it; that is deliberate there and a liability anywhere else. A caller that
only wants to ask a statistics question — "is this n powered?" — must not inherit
that plumbing, so the two functions live in a leaf with no first-party imports at all
and ``cells`` re-exports them.

The proof that the public surface survived the move is not in this file: it is
``tests/test_cells.py::test_wilson_and_detectable_lift_edges``, which calls
``cells.wilson`` / ``cells.detectable_lift`` by attribute and passes unmodified.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
POWER_PY = REPO / "gtm_core" / "power.py"

# Import-only probe: no pytest, no conftest, nothing but the module under test.
_PROBE = """
import sys
import gtm_core.power as power

leaked = [p for p in sys.path if "tests/linter" in p.replace("\\\\", "/")]
assert not leaked, "gtm_core.power dragged the linter dir onto sys.path: %r" % (leaked,)
assert "gtm_core.cells" not in sys.modules, "gtm_core.power pulled in gtm_core.cells"
assert power.detectable_lift(1000, 0.059) is not None
print("ok")
"""


def _first_party_imports(source: str) -> list[str]:
    """Every import in ``source`` that reaches first-party code.

    Relative imports (``level > 0``) and any absolute import rooted at one of this
    repo's own packages count. Everything else is stdlib as far as this check cares.
    """
    first_party_roots = {"gtm_core", "agent", "backend", "cockpit", "mcp_server", "tests"}
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                found.append(f"relative import at line {node.lineno}")
            elif (node.module or "").split(".", 1)[0] in first_party_roots:
                found.append(f"{node.module} at line {node.lineno}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in first_party_roots:
                    found.append(f"{alias.name} at line {node.lineno}")
    return found


def test_power_is_importable_without_touching_sys_path():
    """The reason for the move: cells.py mutates sys.path at import to reach
    tests/linter. gtm_distill must not inherit that plumbing to ask a statistics
    question.

    Run in a subprocess so a prior ``cells`` import elsewhere in this session cannot
    mask the coupling.
    """
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert proc.stdout.strip() == "ok"


def test_power_imports_only_stdlib():
    assert _first_party_imports(POWER_PY.read_text(encoding="utf-8")) == []


def test_the_first_party_import_check_can_fail(tmp_path):
    """§R18 negative control for the AST check: a check that cannot go red is not a
    check. Feed it the exact plumbing power.py must never grow."""
    tainted = "import math\nfrom gtm_core.paths import _safe_segment\nfrom .cells import wilson\n"
    found = _first_party_imports(tainted)
    assert len(found) == 2
    assert any("gtm_core.paths" in f for f in found)
    assert any("relative import" in f for f in found)


def test_the_moved_functions_are_identical_to_the_originals():
    from gtm_core import cells, power

    assert power.wilson is cells.wilson
    assert power.detectable_lift is cells.detectable_lift
    assert (power.Z_CONF, power.Z_POWER) == (cells.Z_CONF, cells.Z_POWER)


def test_the_identity_check_can_fail():
    """§R18 negative control for the identity test: a hand-copied duplicate behaves
    identically and would satisfy an equality assertion, so ``is`` is what makes the
    re-export claim discriminating rather than vacuous."""
    from gtm_core import power

    twin = types.FunctionType(
        power.detectable_lift.__code__,
        power.detectable_lift.__globals__,
        power.detectable_lift.__name__,
    )
    assert twin(1000, 0.059) == power.detectable_lift(1000, 0.059)  # same behaviour
    assert twin is not power.detectable_lift  # and still not the same object
