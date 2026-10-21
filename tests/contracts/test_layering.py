"""Import-layering contract (E-0.4): enforce the package dependency direction the repo's
module-contracts table documents — stdlib-``ast`` only, no import-linter dependency
(mirrors the traversal pattern in scripts/neo4j_code_graph/ingest.py).

Scans first-party source roots only: agent, gtm_core, backend, cockpit, mcp_server.
``plugin/`` is excluded — it's a distribution bundle with vendored copies (e.g.
``plugin/lib/gtm_core/resolve_knowledge.py``), not a real import edge. ``telegram/``
is excluded — it is a README-only directory, not an importable package.

This scan intentionally counts imports under ``if TYPE_CHECKING:`` (they are still
``ast.ImportFrom`` nodes) — a type-only reference is still a layering edge worth
forbidding, which is exactly the gtm_core→agent edge this test was written to catch.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

SOURCE_ROOTS = ("agent", "gtm_core", "backend", "cockpit", "mcp_server")

# Edges allowed FROM src package TO dst package (src != dst). Anything not listed here
# is a layering violation.
ALLOWED_EDGES = {
    ("agent", "gtm_core"),
    ("cockpit", "agent"),
    ("cockpit", "gtm_core"),
    ("backend", "gtm_core"),
    ("backend", "agent"),
    ("mcp_server", "gtm_core"),
    ("mcp_server", "backend"),
}


def find_violations(root: Path = REPO, source_roots=SOURCE_ROOTS, allowed=ALLOWED_EDGES):
    """Return a list of ``"src_module:lineno -> dst_package"`` strings for forbidden edges."""
    import sys

    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from gtm_core.ast_graph import build_graph

    violations = []
    g = build_graph(root, source_roots=source_roots)
    for mod, target, lineno in g.imports:
        pkg = mod.split(".", 1)[0]
        dst_pkg = target.split(".", 1)[0]
        if dst_pkg == pkg or dst_pkg not in source_roots:
            continue
        if (pkg, dst_pkg) not in allowed:
            violations.append(f"{mod}:{lineno} -> {dst_pkg}")
    return violations


def test_no_layering_violations():
    violations = find_violations()
    assert violations == [], (
        "Forbidden cross-package imports found (see the module-contracts table):\n"
        + "\n".join(violations)
    )


def test_checker_self_test_detects_synthetic_violation(tmp_path):
    """Prove the checker actually fires — don't just trust it passes on real code by luck."""
    (tmp_path / "gtm_core").mkdir()
    (tmp_path / "gtm_core" / "__init__.py").write_text("")
    (tmp_path / "gtm_core" / "bad.py").write_text(
        "from agent.config import Config  # forbidden: gtm_core -> agent\n"
    )
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "__init__.py").write_text("")
    (tmp_path / "agent" / "config.py").write_text("")

    violations = find_violations(root=tmp_path, source_roots=("agent", "gtm_core"))
    assert any(v.startswith("gtm_core.bad:") and v.endswith("-> agent") for v in violations)


def test_checker_self_test_allows_permitted_edge(tmp_path):
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "__init__.py").write_text("")
    (tmp_path / "agent" / "uses_core.py").write_text("import gtm_core.models\n")
    (tmp_path / "gtm_core").mkdir()
    (tmp_path / "gtm_core" / "__init__.py").write_text("")
    (tmp_path / "gtm_core" / "models.py").write_text("")

    violations = find_violations(root=tmp_path, source_roots=("agent", "gtm_core"))
    assert violations == []
