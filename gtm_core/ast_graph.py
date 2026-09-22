from __future__ import annotations

import ast
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Dirs never scanned. `content` is runtime tenant state / PII — never ingest it. `.worktrees`
# holds full duplicate checkouts (git worktrees) — walking them duplicates every internal
# name, which collapses INHERITS/CALLS resolution (both require a name to be globally unique).
EXCLUDE_DIRS = {
    ".git",
    ".hg",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "content",
    ".worktrees",
}


def is_excluded(path: Path, root: Path) -> bool:
    parts = path.relative_to(root).parts
    return any(p in EXCLUDE_DIRS or p.endswith(".egg-info") for p in parts)


def module_name(path: Path, root: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or path.stem


@dataclass
class Graph:
    modules: list = field(default_factory=list)
    classes: list = field(default_factory=list)
    functions: list = field(default_factory=list)
    imports: list = field(default_factory=list)  # (src_module, dst_target_dotted, lineno)
    inherits: list = field(default_factory=list)  # (class_qname, base_simple_name)
    calls: list = field(default_factory=list)  # (caller_qname, callee_simple_name)


class Collector(ast.NodeVisitor):
    """Walks one module's AST, appending nodes/edges to the shared Graph."""

    def __init__(self, mod: str, graph: Graph) -> None:
        self.mod = mod
        self.g = graph
        self.scope: list[str] = []  # nested class/def names → qname path
        self.class_stack: list[str] = []  # qnames of enclosing classes
        self.func_stack: list[str] = []  # qnames of enclosing functions (CALLS source)

    def qname(self, name: str) -> str:
        return f"{self.mod}:{'.'.join(self.scope + [name])}"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        q = self.qname(node.name)
        self.g.classes.append(
            {"qname": q, "name": node.name, "module": self.mod, "lineno": node.lineno}
        )
        for base in node.bases:
            name = self._simple_name(base)
            if name:
                self.g.inherits.append((q, name))
        self.scope.append(node.name)
        self.class_stack.append(q)
        for child in node.body:
            self.visit(child)
        self.class_stack.pop()
        self.scope.pop()

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        q = self.qname(node.name)
        self.g.functions.append(
            {
                "qname": q,
                "name": node.name,
                "module": self.mod,
                "lineno": node.lineno,
                "is_method": bool(self.class_stack),
                "class_qname": self.class_stack[-1] if self.class_stack else None,
            }
        )
        self.scope.append(node.name)
        self.func_stack.append(q)
        for child in node.body:
            self.visit(child)
        self.func_stack.pop()
        self.scope.pop()

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function

    def visit_Call(self, node: ast.Call) -> None:
        callee = self._simple_name(node.func)
        if callee and self.func_stack:
            self.g.calls.append((self.func_stack[-1], callee))
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.g.imports.append((self.mod, alias.name, node.lineno))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = node.module or ""
        if node.level:  # relative import → resolve against the current package
            anchor = self.mod.split(".")
            anchor = anchor[: max(0, len(anchor) - node.level)]
            base = ".".join([*anchor, base]) if base else ".".join(anchor)
        if not base:
            return
        self.g.imports.append((self.mod, base, node.lineno))
        for alias in node.names:  # `from pkg import sub` may name a submodule
            self.g.imports.append((self.mod, f"{base}.{alias.name}", node.lineno))

    @staticmethod
    def _simple_name(node) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None


def build_graph(root: Path, source_roots: tuple[str, ...] | list[str] | None = None) -> Graph:
    g = Graph()
    if source_roots is not None:
        paths = []
        for pkg in source_roots:
            pkg_dir = root / pkg
            if pkg_dir.is_dir():
                paths.extend(pkg_dir.rglob("*.py"))
    else:
        paths = root.rglob("*.py")

    for path in sorted(p for p in paths if not is_excluded(p, root)):
        mod = module_name(path, root)
        try:
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src, filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            print(f"  skip {path.relative_to(root)}: {exc}", file=sys.stderr)
            continue
        g.modules.append(
            {
                "path": str(path.relative_to(root)),
                "module": mod,
                "package": mod.split(".")[0],
                "lines": src.count("\n") + 1,
            }
        )
        Collector(mod, g).visit(tree)
    return g


def resolve(g: Graph):
    """Filter raw edges down to precise, internal-only relationships."""
    module_names = {m["module"] for m in g.modules}
    internal_imports = {(s, d) for s, d, _lineno in g.imports if d in module_names and d != s}

    def unique(rows):
        by_name: dict[str, set] = defaultdict(set)
        for r in rows:
            by_name[r["name"]].add(r["qname"])
        return {n: next(iter(q)) for n, q in by_name.items() if len(q) == 1}

    uniq_cls, uniq_fn = unique(g.classes), unique(g.functions)
    inherits = {(cq, uniq_cls[b]) for cq, b in g.inherits if b in uniq_cls and uniq_cls[b] != cq}
    calls = {(cq, uniq_fn[c]) for cq, c in g.calls if c in uniq_fn and uniq_fn[c] != cq}
    return sorted(internal_imports), sorted(inherits), sorted(calls)
