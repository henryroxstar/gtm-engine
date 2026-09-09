"""Shared extractors for the deploy-surface contracts (T1/T14 and T2/T5).

NOT a test module (hence the leading underscore, matching ``tests/backend/_protocol1.py``).
It holds the parsers that both ``test_runtime_surface_mounted.py`` and
``test_deploy_paths_cover_shipped_surfaces.py`` derive their assertions from, so the two
files cannot drift apart on what "the image bakes" or "compose mounts" means.

Every function here is pure text/AST over a string or a directory — no repo constants, no
I/O of its own — so the checker self-tests can drive them with synthetic trees under
``tmp_path``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

__all__ = [
    "copy_targets",
    "deploy_path_filter",
    "mount_sources",
    "normalise",
    "repo_root_literals",
    "undelivered",
]


def normalise(raw: str) -> str:
    """``'./plugin/'`` → ``plugin``; ``${GTM_CONTENT_DIR:-./content}`` → ``content``;
    ``'agent/**'`` → ``agent``."""
    val = raw.strip().strip("'\"")
    # Strip a ${VAR:-default} wrapper, keeping the default — the path actually used.
    m = re.fullmatch(r"\$\{[A-Z_][A-Z0-9_]*:-(.*)\}", val)
    if m:
        val = m.group(1)
    val = val.removeprefix("./")
    val = re.sub(r"/\*\*$", "", val)  # a glob suffix from a workflow paths: filter
    return val.rstrip("/")


def copy_targets(dockerfile_text: str) -> set[str]:
    """Paths the image bakes, from ``COPY <src> <dst>`` lines."""
    out: set[str] = set()
    for line in dockerfile_text.splitlines():
        m = re.match(r"\s*COPY\s+(?:--\S+\s+)*(\S+)\s+\S+", line)
        if m:
            out.add(normalise(m.group(1)))
    return out


def mount_sources(compose_text: str) -> set[str]:
    """Paths the compose file bind-mounts, from ``source:`` entries."""
    return {
        normalise(m.group(1))
        for m in re.finditer(r"^\s*source:\s*(\S+)\s*$", compose_text, re.MULTILINE)
    }


def deploy_path_filter(workflow_text: str) -> set[str]:
    """Entries under the workflow's ``paths:`` key, normalised.

    Reads the first ``paths:`` block and stops at the first line that is neither a list
    item nor blank — enough for a GitHub workflow's flat list, and it fails the
    non-vacuity companion loudly if the shape ever changes.
    """
    out: set[str] = set()
    in_block = False
    for line in workflow_text.splitlines():
        if re.match(r"^\s*paths:\s*$", line):
            in_block = True
            continue
        if not in_block:
            continue
        m = re.match(r"^\s*-\s*(\S+)", line)
        if m:
            out.add(normalise(m.group(1)))
        elif line.strip() and not line.lstrip().startswith("#"):
            break
    return out


def repo_root_literals(tree_root: Path) -> set[str]:
    """String literals joined to ``*.repo_root`` with ``/`` anywhere under ``tree_root``.

    AST-based rather than textual so a docstring or comment naming a directory can never
    false-positive — only executable path construction is inspected.
    """
    found: set[str] = set()
    for path in sorted(tree_root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:  # a deliberately-broken fixture is not this checker's problem
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
                continue
            left, right = node.left, node.right
            is_repo_root = (isinstance(left, ast.Attribute) and left.attr == "repo_root") or (
                isinstance(left, ast.Name) and left.id == "repo_root"
            )
            if is_repo_root and isinstance(right, ast.Constant) and isinstance(right.value, str):
                found.add(normalise(right.value))
    return found


def undelivered(wanted: set[str], delivered: set[str]) -> set[str]:
    """Which of ``wanted`` no deploy surface delivers."""
    return {item for item in wanted if item not in delivered}
