"""The backend image can import every ``gtm_core`` module that reaches into ``tests/linter``.

Several ``gtm_core`` modules put ``tests/linter`` on ``sys.path`` and import the ``outreach``
package from it — deliberately, so the seat map and the merge-tag renderer have one home, the
linter that enforces them. ``deploy/Dockerfile.backend`` copied ``gtm_core/`` but not
``tests/``, so on staging those imports raised ``ModuleNotFoundError: No module named
'outreach'`` and a pack run's quality checks quietly did not run (client issue #337 rerun).

This builds a tree from exactly the directories the Dockerfile ``COPY``s and imports the
modules from it in a fresh interpreter, so a COPY line removed (or a new ``tests/<x>`` reach
added without one) fails here rather than on the box.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO / "deploy" / "Dockerfile.backend"

_REACH = re.compile(r'"tests"\s*/\s*"(\w+)"')

if not DOCKERFILE.exists():
    pytest.skip("deploy/ is not present (private surface)", allow_module_level=True)


def _copied_dirs() -> list[str]:
    """Source directories the Dockerfile COPYs from the build context (``COPY src/ dst/``)."""
    out = []
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*COPY\s+(?!--from)(\S+)/\s+\S+", line)
        if m:
            out.append(m.group(1))
    return out


def _import_reaches() -> dict[str, set[str]]:
    """``{module: {tests subtree}}`` for every gtm_core module that puts a ``tests/<x>`` directory
    on ``sys.path`` to import from it. A module that only reads a data file under ``tests/`` (an
    advisory allowlist, say) is not an import and is not counted."""
    out: dict[str, set[str]] = {}
    for path in sorted((REPO / "gtm_core").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        found = set(_REACH.findall(text))
        if found and "sys.path" in text:
            module = ".".join(path.relative_to(REPO).with_suffix("").parts)
            out[module] = found
    return out


def test_every_tests_subtree_gtm_core_reaches_into_is_copied():
    reached = set().union(*_import_reaches().values())
    assert reached, "the scan found no tests/<x> reach at all: the pattern is stale"
    missing = sorted(f"tests/{d}" for d in reached if f"tests/{d}" not in _copied_dirs())
    assert not missing, f"gtm_core imports from {missing}, which Dockerfile.backend never COPYs"


def test_the_linter_reaching_modules_import_from_the_image_tree(tmp_path):
    # Only the trees these imports can touch: copying all of plugin/ and profiles/ is slow and
    # proves nothing more. Everything else they need is an installed dependency.
    for rel in _copied_dirs():
        if rel == "gtm_core" or rel.startswith("tests/"):
            shutil.copytree(
                REPO / rel, tmp_path / rel, ignore=shutil.ignore_patterns("__pycache__")
            )
    code = (
        "import importlib, sys\n"
        f"sys.path.insert(0, {str(tmp_path)!r})\n"
        f"for name in {sorted(_import_reaches())!r}:\n"
        "    mod = importlib.import_module(name)\n"
        f"    assert mod.__file__.startswith({str(tmp_path)!r}), mod.__file__\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, done.stderr[-2000:]
