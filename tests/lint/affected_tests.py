#!/usr/bin/env python3
"""Select the test files that exercise a set of changed ``.py`` files — the Definition-of-Done
"every test file that imports a module you changed" gate.

The gate this replaces was ``grep -rlF "<dotted.module.path>" tests --include='test_*.py'``. A
substring grep for ``pkg.sub.mod`` cannot see ``from pkg.sub import mod`` — the dotted path never
appears on that line — so the gate under-selected silently, and a branch that changed several
modules of one package ran a fraction of the tests that exercised them. A missed test run is the
failure; an extra one is harmless. So a file is *affected* when ANY of these hold (the first is
the old grep, kept verbatim, so this can only ever select more):

  1. its text contains a changed module's dotted path (``patch("pkg.sub.mod.fn")``,
     ``import_module("pkg.sub.mod")``, ``["-m", "pkg.sub.mod"]``, the plain import forms);
  2. its text contains a changed file's repo-relative path (``spec_from_file_location(...,
     ROOT / "tests/lint/x.py")``);
  3. an ``import`` / ``from ... import`` anywhere in it (lazy imports included) names a changed
     module, a submodule of one (which executes a changed ``__init__``), or — for
     ``from pkg.sub import mod`` — resolves ``mod`` as the module ``pkg.sub.mod``.

A changed module is matched under two names: its dotted path from the repo root, and its dotted
path from its pytest *basedir* (the nearest ancestor directory without an ``__init__.py``) — the
second is how ``import pii_check`` reaches ``tests/lint/pii_check.py`` via a ``sys.path`` insert.

Shared helpers under ``tests/`` (any non-``test_*.py`` module there) propagate: a helper that is
affected counts as changed, to a fixpoint, so a test that reaches the change only through
``from tests.x._helpers import ...`` is selected. Imports are NOT followed through production
code — that closure is most of the suite, which is CI's job. A changed ``test_*.py`` selects
itself. A file that does not parse is treated as affected, with a warning on stderr: an unreadable
file is not evidence it is unaffected.

Usage:
    python tests/lint/affected_tests.py --base origin/dev      # branch diff + worktree + untracked
    python tests/lint/affected_tests.py path/to/changed.py ...  # explicit paths

Prints the selected test files, sorted, one per line (nothing when none). Non-``.py`` paths are
ignored. Stdlib only.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def changed_paths(base: str, root: Path = ROOT) -> list[str]:
    """Every path changed on the branch since ``base``, in the worktree, or untracked."""
    cmds = (
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        ["git", "diff", "--name-only", "HEAD"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    )
    out: set[str] = set()
    for cmd in cmds:
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        if proc.returncode != 0:
            # A bad ref must not read as "nothing changed" — that is the silent under-selection.
            raise SystemExit(f"affected_tests: `{' '.join(cmd)}` failed: {proc.stderr.strip()}")
        out.update(line for line in proc.stdout.splitlines() if line)
    return sorted(out)


def is_test_file(rel: str) -> bool:
    return rel.startswith("tests/") and rel.endswith(".py") and Path(rel).name.startswith("test_")


def dotted_path(rel: str) -> str:
    """``a/b/c.py`` -> ``a.b.c``; ``a/b/__init__.py`` -> ``a.b`` (the old recipe's mapping)."""
    parts = list(Path(rel).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def module_names(rel: str, root: Path = ROOT) -> set[str]:
    """The dotted names ``rel`` is importable as: from the repo root, and from its basedir."""
    full = dotted_path(rel)
    if not full:
        return set()
    parts = full.split(".")
    # Directories holding the module: every part for a package ``__init__``, else all but the last.
    i = len(parts) if Path(rel).name == "__init__.py" else len(parts) - 1
    while i > 0 and (root.joinpath(*parts[:i]) / "__init__.py").is_file():
        i -= 1
    return {full, ".".join(parts[i:])}


def imported_names(tree: ast.AST, rel: str) -> set[str]:
    """Every dotted name an import in ``tree`` could bind to a module."""
    package = list(Path(rel).with_suffix("").parts[:-1])
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = package[: len(package) - node.level + 1] if node.level else []
            base = ".".join([*prefix, *([node.module] if node.module else [])])
            if base:
                names.add(base)
            names.update(
                f"{base}.{a.name}" if base else a.name for a in node.names if a.name != "*"
            )
    return names


class _Target:
    def __init__(self, rel: str, root: Path) -> None:
        self.rel = rel
        self.dotted = dotted_path(rel)
        self.names = module_names(rel, root)

    def reached_by(self, text: str, imported: set[str] | None) -> bool:
        if (self.dotted and self.dotted in text) or self.rel in text:
            return True
        if imported is None:  # unparseable: assume affected
            return True
        return any(n == m or n.startswith(m + ".") for n in imported for m in self.names)


def _scan(path: Path, rel: str) -> tuple[str, set[str] | None]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        return text, imported_names(ast.parse(text, filename=rel), rel)
    except SyntaxError as exc:
        print(f"affected_tests: treating unparseable {rel} as affected: {exc}", file=sys.stderr)
        return text, None


def select(changed: list[str], root: Path = ROOT) -> list[str]:
    changed_py = sorted({p for p in changed if p.endswith(".py")})
    selected = {rel for rel in changed_py if is_test_file(rel) and (root / rel).is_file()}
    targets = [_Target(rel, root) for rel in changed_py if not is_test_file(rel)]
    if not targets:
        return sorted(selected)

    tests: dict[str, Path] = {}
    helpers: dict[str, Path] = {}
    for path in sorted((root / "tests").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        (tests if is_test_file(rel) else helpers)[rel] = path
    scans: dict[str, tuple[str, set[str] | None]] = {}

    def affected(rel: str, path: Path) -> bool:
        if rel not in scans:
            scans[rel] = _scan(path, rel)
        return any(t.reached_by(*scans[rel]) for t in targets)

    # Helpers propagate to a fixpoint: each pass may make another helper reachable.
    pending = {rel: p for rel, p in helpers.items() if rel not in {t.rel for t in targets}}
    grew = True
    while grew:
        grew = False
        for rel in sorted(pending):
            if affected(rel, pending[rel]):
                targets.append(_Target(rel, root))
                del pending[rel]
                grew = True

    selected.update(
        rel for rel, path in tests.items() if rel not in selected and affected(rel, path)
    )
    return sorted(selected)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="affected_tests", description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", help="changed files, repo-relative")
    ap.add_argument("--base", metavar="REF", help="also select for everything changed since REF")
    args = ap.parse_args(argv)
    if not args.paths and not args.base:
        ap.error("give --base REF or at least one path")
    changed = list(args.paths) + (changed_paths(args.base) if args.base else [])
    for rel in select(changed):
        print(rel)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
