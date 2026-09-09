"""A repo-root walk-up must match the file's own depth (PRD 2026-09-01 §6.1).

``Path(__file__).resolve().parents[N]`` is the repo's normal way to find its own root, and it
is silently wrong the moment the file MOVES. Phase 3A hit this twice in one afternoon: both
``brief_lint.ROOT`` and ``content_quality._repo_root()`` used ``parents[1]``, correct from
``gtm_core/<name>.py`` and one level short from ``gtm_core/<name>/model.py`` — so they resolved
to ``gtm_core/`` and every repo-relative lookup under them missed.

Nothing about that is an ``ImportError``. ``content_quality`` went on to report
``"content linter failed: can't open file …/gtm_core/tests/linter/content_linter.py"`` as a
**blocking** finding on content that was completely fine — a gate failing closed for a reason
that had nothing to do with the content. A CLI golden caught it; an import walk never would.

So the depth is checked arithmetically instead of trusted: a file ``a/b/c.py`` is three path
segments from the repo, so ``parents[2]`` is the root and any other index is a bug. This runs
over every source root, which is what makes it a *guard* for the remaining splits rather than a
note about two files that have already been fixed.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = ("agent", "gtm_core", "backend", "cockpit", "mcp_server")
SKIP_PARTS = {"__pycache__", ".venv", "node_modules"}

#: Names whose value is meant to BE the repo root. A walk-up bound to anything else (a package
#: dir, an asset dir) is deliberate and not this test's business.
ROOT_NAMES = {"ROOT", "REPO", "REPO_ROOT", "_REPO_ROOT", "repo_root", "_repo_root"}


def _walk_up_index(node: ast.AST) -> int | None:
    """The N in ``Path(__file__).resolve().parents[N]``, or None if that isn't the shape."""
    if not isinstance(node, ast.Subscript):
        return None
    value, index = node.value, node.slice
    if not (isinstance(value, ast.Attribute) and value.attr == "parents"):
        return None
    if not isinstance(index, ast.Constant) or not isinstance(index.value, int):
        return None
    return index.value if "__file__" in ast.dump(value) else None


def _offenders() -> list[str]:
    bad: list[str] = []
    for root in SOURCE_ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            if SKIP_PARTS & set(path.parts):
                continue
            rel = path.relative_to(REPO)
            expected = len(rel.parts) - 1  # a/b/c.py -> parents[2] is the repo
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                # ROOT = Path(__file__).resolve().parents[N]
                if isinstance(node, ast.Assign):
                    names = {t.id for t in node.targets if isinstance(t, ast.Name)}
                    if not names & ROOT_NAMES:
                        continue
                    found = _walk_up_index(node.value)
                # def _repo_root(): return Path(__file__).resolve().parents[N]
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name not in ROOT_NAMES:
                        continue
                    found = next(
                        (
                            idx
                            for inner in ast.walk(node)
                            if isinstance(inner, ast.Return) and inner.value is not None
                            for idx in [_walk_up_index(inner.value)]
                            if idx is not None
                        ),
                        None,
                    )
                else:
                    continue
                if found is not None and found != expected:
                    bad.append(f"{rel}: parents[{found}] — this file needs parents[{expected}]")
    return bad


def test_every_repo_root_walk_up_matches_its_files_depth():
    offenders = _offenders()
    assert offenders == [], (
        "a repo-root walk-up does not match its file's depth — it will resolve to a parent "
        "directory and every repo-relative path under it will miss, silently:\n  "
        + "\n  ".join(offenders)
    )


def test_the_check_can_actually_fail():
    """Positive control: the arithmetic, exercised on a path whose depth is known.

    Without this the test above passes just as happily if `_walk_up_index` stops matching the
    shape it is looking for — a green gate that reads nothing.
    """
    tree = ast.parse("ROOT = Path(__file__).resolve().parents[1]")
    assign = tree.body[0]
    assert _walk_up_index(assign.value) == 1
    assert _walk_up_index(ast.parse("x = 1").body[0].value) is None
