"""No step a skill runs may reach code whose job is to delete tenant data.

Generalises :mod:`tests.unit.test_no_automatic_purge` from one hand-written list of build steps
to every module a shipped skill actually cites, and from one hop to the whole import graph.

The 2026-09-20 incident: ``retention_sweep.sweep_stale_pii(ttl_days=7)`` was wired into the end of
``prospects_consolidate`` — no flag, no dry run, no output, return value discarded. Three lines,
inside a 108-file squashed merge titled after three unrelated fixes. It purged the only record of
which accounts had asked not to be contacted. Nothing at commit time could see it, because the
reviewer would have had to notice one import in seven thousand changed lines.

The rule this encodes: **deletion is a thing the operator asks for by name.** A module reachable
from a skill-cited command is a build step, and a build step may not reach a destructive module.
If a run needs to remove data, the operator runs the destructive command themselves, and it shows
a plan before it touches anything.

Both halves are derived, not hand-listed, so the gate keeps working as the skills change:

* the **roots** come from the ``python -m gtm_core.<module>`` commands in shipped skill text;
* the **edges** come from the AST, including imports written inside a function body — which is
  exactly how the incident's import was written.

Only :data:`DESTRUCTIVE_MODULES` is a human decision, and it is meant to stay short. Adding a
module here is a claim that its purpose is removal; adding one is not a routine change.

Run standalone::

    uv run python tests/lint/destructive_reachability.py
"""

from __future__ import annotations

import ast
import re
import sys
from collections import deque
from functools import cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GTM_CORE = REPO / "gtm_core"
SKILLS = REPO / "plugin" / "skills"

#: Modules whose PURPOSE is removing tenant data. Short on purpose — see the module docstring.
DESTRUCTIVE_MODULES: frozenset[str] = frozenset({"gtm_core.retention_sweep", "gtm_core.snapshots"})

#: Destructive modules an operator invokes directly. These are roots we expect to be destructive,
#: so they are exempt as roots — they are never exempt as a *destination* from another root.
OPERATOR_OWNED: frozenset[str] = frozenset(
    {"gtm_core.retention_sweep", "gtm_core.snapshots", "gtm_core.signal_sources"}
)

#: Modules that are NAMED like a read and must stay one. ``violations()`` already checks every
#: skill-cited root, so this set adds one thing: it asserts the module is *still cited*, which is
#: what stops the check passing vacuously the day someone drops the citation. A module here is a
#: claim that its whole purpose is computing an answer — adding one is not a routine change.
#:
#: ``gtm_core.scorecard`` scores accounts. It deletes, archives, moves and truncates nothing, and
#: it will be called from ``score``-shaped steps, which is exactly the shape the 2026-09-20
#: incident wore: a build step that had quietly grown a purge.
ASSERTED_CLEAN: frozenset[str] = frozenset({"gtm_core.scorecard"})

_CITED = re.compile(r"-m\s+(gtm_core(?:\.[A-Za-z_]\w*)*)")


def module_name(path: Path) -> str:
    """``gtm_core/lanes/router.py`` -> ``gtm_core.lanes.router`` (a package -> its own name)."""
    rel = path.relative_to(REPO).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve(node: ast.ImportFrom, here: str, is_package: bool = False) -> str:
    """Absolute name for a possibly-relative ``from . import x`` inside module ``here``.

    ``is_package`` matters and was missing until 2026-09-22. :func:`module_name` already pops
    ``__init__``, so for a package's own ``__init__.py`` ``here`` IS the package — stripping a
    level then climbs one too far, and every relative import in every package ``__init__``
    resolved to a module that does not exist (``gtm_core.scorecard``'s ``from .score import ...``
    became ``gtm_core.score``). :func:`reaches` skips unknown names, so those packages had **zero
    outgoing edges** and were reported clean against any target whatsoever — a check that could
    not fail, which is the §R18 shape this gate exists to catch in other people's code.
    """
    if not node.level:
        return node.module or ""
    # level 1 = this module's package. A package __init__ is already AT its package.
    climb = node.level - 1 if is_package else node.level
    pkg = here.split(".")[:-climb] if climb and "." in here else here.split(".")
    return ".".join([*pkg, *([node.module] if node.module else [])])


def referenced_modules(path: Path) -> set[str]:
    """Every ``gtm_core.*`` module this file imports, at module level or inside a function."""
    return refs_from_source(
        path.read_text(encoding="utf-8"), module_name(path), is_package=path.name == "__init__.py"
    )


def refs_from_source(src: str, here: str, *, is_package: bool = False) -> set[str]:
    """The same, from source text — so a test can hand it a file from git history."""
    out: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve(node, here, is_package)
            if base:
                out.add(base)
                # `from gtm_core import retention_sweep` imports a MODULE, not a name.
                out.update(f"{base}.{a.name}" for a in node.names)
    return {m for m in out if m == "gtm_core" or m.startswith("gtm_core.")}


@cache
def import_graph() -> dict[str, frozenset[str]]:
    """Module -> the ``gtm_core`` modules it references, for every file under ``gtm_core/``."""
    return {
        module_name(p): frozenset(referenced_modules(p))
        for p in sorted(GTM_CORE.rglob("*.py"))
        if "__pycache__" not in p.parts
    }


@cache
def skill_cited_modules() -> frozenset[str]:
    """Every ``python -m gtm_core.<module>`` a shipped skill names, as module names."""
    # `references/**/*.md`: the provider adapters are one level deeper and were reached
    # by neither this scan nor the CLI-contract lint until 2026-09-21.
    docs = [*SKILLS.glob("*/SKILL.md"), *SKILLS.glob("*/references/**/*.md")]
    cited = {m for doc in docs for m in _CITED.findall(doc.read_text(encoding="utf-8"))}
    known = set(import_graph())
    # A citation may name a package (`gtm_core.prospects_consolidate`) or a module inside it.
    return frozenset(c for c in cited if c in known)


def reaches(
    graph: dict[str, frozenset[str]], start: str, targets: frozenset[str]
) -> list[str] | None:
    """The shortest import chain from ``start`` to any target, or ``None``. The chain IS the
    error message: "which build step" is useless without "by what route"."""
    queue: deque[list[str]] = deque([[start]])
    seen = {start}
    while queue:
        chain = queue.popleft()
        for nxt in sorted(graph.get(chain[-1], ())):
            if nxt in targets:
                return [*chain, nxt]
            if nxt in seen or nxt not in graph:
                continue
            seen.add(nxt)
            queue.append([*chain, nxt])
    return None


def uncited_assertions() -> list[str]:
    """Names in :data:`ASSERTED_CLEAN` that no shipped skill cites any more.

    An assertion about a module nothing runs is an assertion that cannot fail. This is the
    instrument check for the set above.
    """
    return sorted(ASSERTED_CLEAN - skill_cited_modules())


def edgeless_assertions() -> list[str]:
    """Names in :data:`ASSERTED_CLEAN` whose outgoing edges do not resolve to real modules.

    The second instrument check, and the one that was missing. A root whose imports all resolve
    to names absent from the graph has NO outgoing edges, so :func:`reaches` returns ``None`` for
    every target and "reaches nothing destructive" holds trivially. That is what the
    package-``__init__`` off-by-one in :func:`_resolve` produced until 2026-09-22: a clean
    verdict that was a bug in the instrument, not a fact about the code.
    """
    graph = import_graph()
    return sorted(m for m in ASSERTED_CLEAN if not (graph.get(m, frozenset()) & set(graph)))


def violations() -> list[tuple[str, list[str]]]:
    graph = import_graph()
    found = []
    for root in sorted(skill_cited_modules() - OPERATOR_OWNED):
        chain = reaches(graph, root, DESTRUCTIVE_MODULES)
        if chain:
            found.append((root, chain))
    return found


def main() -> int:
    bad = violations()
    for root, chain in bad:
        print(f"{root}: reaches {chain[-1]} via {' -> '.join(chain)}", file=sys.stderr)
    if bad:
        print(
            "\nDeletion is the operator's explicit command, never a build step. Move the call out "
            "of the build path, or give the destructive module a plan-first CLI of its own.",
            file=sys.stderr,
        )
        return 1
    print(f"ok: {len(skill_cited_modules())} skill-cited modules reach nothing destructive")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
