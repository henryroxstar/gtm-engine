"""The other direction of the `profiles/` writer set: is there a writer the table misses?

**The gap this closes.** Until 2026-09-24 the writer set in ``docs/RULES.md`` ("The writer set
for ``profiles/``, stated once") was enforced **doc↔doc only**:
``tests/unit/test_messaging_angle_status.py`` counted the rows, and
``tests/contracts/test_icp_check_is_read_only.py`` banned importing the names those rows yield —
inside ``gtm_core/icp_check/**`` and nowhere else. Nothing read ``gtm_core/**`` and asked the
opposite question. So a new writer was invisible to CI by construction, and two of them were:
``messaging.matrix_view`` shipped in the same working tree as ``messaging.angle_status`` and was
not tabled, and ``voc.registry.apply`` had been appending to ``knowledge/competitors.toml``
unlisted for months. The only reason the first was banned from ``icp_check`` at all is that the
derived ban key is the coarse package name ``messaging`` — by accident of granularity, not
because any check knew the writer existed.

**The blind spot that made it green with a writer present.** On 2026-09-24, one day after this
file shipped, it reported green while ``knowledge_meta.seed_file`` was prepending frontmatter to
every managed topic in a profile. Taint crossed ``=``, ``:=``-style annotated assignment and
``with … as``; it did **not** cross a ``for`` target or a comprehension target — and
``knowledge_meta`` reaches its write through nothing else
(``for root, prefix in managed_roots(...)``, whose return is a list *comprehension*, then
``for path in iter_managed_topics(root)``, whose return is an accumulated list). A detector that
stops at the binding forms it happened to be written against answers "the shapes I check are
clean", not "the tree is clean". The forms below were added in response, and every one of them
has a §R12 control:

**What this test does.** It taints the three functions that hand out a path under ``profiles/``
(:func:`gtm_core.paths.resolve_knowledge_file`, :func:`~gtm_core.paths.resolve_profiles_root`,
:func:`~gtm_core.paths.workspace_profiles_root`) plus any parameter **or attribute** named
``profiles_root`` / ``profile_root``, propagates that taint through assignments, ``for`` and
comprehension targets, ``/`` joins, ``Path(...)``, ``sorted``/``list``-style passthroughs, list
accumulation (``out.append(path)``) and path-returning attributes, and fails on any filesystem
write whose target is tainted in a module ``RULES.md`` does not name. Taint crosses function
boundaries in two ways: a function that *returns or yields* a tainted value becomes a producer
itself (which is how ``matrix_view.matrix_path``, ``knowledge_staging.live_path`` and
``knowledge_meta.iter_managed_topics`` are reached), and an argument passed tainted at a call
site taints that parameter in the callee (which is how ``matrix_view.write_matrix``, whose
target is a bare ``path`` parameter, is reached from ``messaging/cli.py``). Both run to a
fixpoint.

**Tuple taint is element-wise where the shape is knowable**, and that precision is load-bearing
rather than polish. Treating ``profile, reg = _load(args)`` as tainting *both* names makes the
profile SLUG a path; ``paths._safe_segment`` then returns it, becomes a "producer", and every
module that validates a slug — fifty of them, nearly all writing under ``content/`` — lights up
as a ``profiles/`` writer. A finding list that long is one nobody reads. So a function records
what its return, and what iterating its return, hands out per element, and an unpacking binding
consults it; a declared ``-> str`` return ends taint outright.

**What it CANNOT see — stated, because a detector with unwritten blind spots is one the next
reader over-trusts:**

* **A path built from a string literal.** ``Path("profiles") / profile / ...`` is a write under
  ``profiles/`` that no resolver produced, and nothing here taints it. The naming convention
  (``profiles_root``) is the only reason ``funnel.record_actuals`` and ``knowledge_meta`` are
  caught, and a parameter or attribute named anything else would not be.
* **Dynamic dispatch.** A write through ``getattr``, a dict of callables, a subclass method
  (``vfs.local.LocalVFS.write_text``), or a callback handed in from outside ``gtm_core`` is
  resolved nowhere and taints nothing.
* **Call targets by name only within `gtm_core/`.** A helper called through a re-export, a
  method on a class instance, or from ``agent/`` / ``backend/`` / a skill's shell command is not
  in the call graph, so its parameters are never tainted from that call site.
* **Writes outside `gtm_core/`.** ``agent/``, ``backend/`` and ``cockpit/`` are not scanned.
* **A path carried inside a container other than a tuple element or a list of paths.** A dict
  value, a dataclass field, a ``NamedTuple`` attribute or an object the scanner cannot type is
  not followed. ``_tuple_shape`` knows tuples, lists and comprehensions of them; everything
  else falls back to whole-value taint, and a ``dict[str, Path]`` falls back to nothing.
* **An unpacking whose shape the scanner cannot read.** Element-wise taint needs the callee's
  return to be a literal tuple/list or a comprehension of one. ``a, b = something_opaque()``
  taints both names (over-approximate) or neither (if the call is untainted) — never the right
  one of the two.
* **Taint that would need real scoping.** Comprehension targets leak into the enclosing
  function's taint set, and a name rebound to something untainted stays tainted. Both are
  deliberate over-approximations: this check is allowed to ask a question, never to miss one.
* **A module-level write.** Only function bodies are visited, so a write at import time is
  invisible.
* **Whether a tabled writer is still key-scoped.** This answers "is the module in the table",
  never "is the write as narrow as the table says". That question stays with the per-writer
  tests (``test_angle_status_writer_is_key_scoped`` and its siblings). It is also why the table
  row is a MODULE: ``hooks.migrate_from_matrix`` writes ``hooks.toml`` too and is covered by the
  ``hooks.save_hooks`` row's module, not by a row of its own.

Consequently a green run means *no write reachable by taint* is untabled — not *no write
exists*. The §R12-style controls below plant a writer in each supported binding form and assert
the detector finds it, so a green run can never mean the detector found nothing at all.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_PACKAGE = _REPO / "gtm_core"

#: The functions that hand out a path under `profiles/`, as ``(module, function)``.
_SEED_PRODUCERS = frozenset(
    {
        ("paths", "resolve_knowledge_file"),
        ("paths", "resolve_profiles_root"),
        ("paths", "workspace_profiles_root"),
    }
)

#: Parameter names this repo uses, by convention, for an already-resolved profiles root. A
#: convention and not a guarantee — see the blind-spot list in the module docstring — but it is
#: what reaches `funnel.record_actuals`, whose target is built from a bare `profile_root`.
_ROOT_PARAMS = frozenset({"profiles_root", "profile_root"})

#: Attributes and methods that return another path, so taint survives them.
_PATH_STEPS = frozenset(
    {
        "parent",
        "with_suffix",
        "with_name",
        "with_stem",
        "joinpath",
        "resolve",
        "expanduser",
        "absolute",
    }
)

#: Methods that return an ITERABLE of paths under their receiver. Taint survives them too, but
#: only through a binding that iterates — a `for` target or a comprehension target.
_PATH_ITER_STEPS = frozenset({"iterdir", "glob", "rglob"})

#: Builtins that hand back the same elements in another container. `sorted(dir.rglob(...))` is
#: the shape every "all topics under this root" helper in the tree uses.
_PASSTHROUGH_CALLS = frozenset({"sorted", "list", "tuple", "set", "frozenset", "reversed", "iter"})

#: Mutating methods that put their ARGUMENT inside their RECEIVER, so a tainted element makes
#: the accumulator tainted. `out = []` / `out.append(path)` / `return out` is how
#: `knowledge_meta.iter_managed_topics` hands out every managed topic path.
_ACCUMULATE_ELEMENT = frozenset({"append", "add"})
_ACCUMULATE_CONTAINER = frozenset({"extend", "update"})

#: Methods that write, delete or create at their RECEIVER.
_METHOD_WRITES = frozenset(
    {"write_text", "write_bytes", "mkdir", "touch", "unlink", "rename", "symlink_to", "hardlink_to"}
)

#: ``module.function`` writes, mapped to the argument indices that name a path being written.
_QUALIFIED_WRITES = {
    ("os", "replace"): (0, 1),
    ("os", "rename"): (0, 1),
    ("os", "remove"): (0,),
    ("os", "unlink"): (0,),
    ("shutil", "move"): (0, 1),
    ("shutil", "copy"): (1,),
    ("shutil", "copy2"): (1,),
    ("shutil", "copyfile"): (1,),
    ("shutil", "copytree"): (1,),
    ("shutil", "rmtree"): (0,),
}

#: In-repo write helpers, as ``(module, function)``; the path is argument 0.
_HELPER_WRITES = frozenset({("fsio", "atomic_write_text"), ("fsio", "atomic_write_bytes")})

#: Modules that hold a tainted write and are deliberately NOT in the RULES.md table, each with
#: the reason. Kept to the mechanism only: `fsio.atomic_write_text` writes wherever its caller
#: points it, so it is flagged by every caller's taint without being a writer of anything on its
#: own. Its callers ARE tabled, which is where the guarantee lives. Anything else added here
#: would be a writer nobody is enforcing — the exact failure this file exists to catch.
_MECHANISM_ONLY = frozenset({"fsio"})


def _module_key(path: Path) -> str:
    parts = list(path.relative_to(_PACKAGE).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


class _Module:
    """One source file, with enough import resolution to name a call's target module."""

    def __init__(self, key: str, source: str) -> None:
        self.key = key
        self.tree = ast.parse(source)
        self.funcs = {
            n.name: n
            for n in ast.walk(self.tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        self.names: dict[str, tuple[str, str]] = {}
        self.aliases: dict[str, str] = {}
        self._read_imports()

    def _read_imports(self) -> None:
        base = self.key.split(".")[:-1]
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                target = self._target_module(node, base)
                if target is None:
                    continue
                for alias in node.names:
                    local = alias.asname or alias.name
                    self.names[local] = (target, alias.name)
                    self.aliases[local] = ".".join(p for p in (target, alias.name) if p)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("gtm_core."):
                        short = alias.name[len("gtm_core.") :]
                        self.aliases[alias.asname or short.split(".")[-1]] = short

    def _target_module(self, node: ast.ImportFrom, base: list[str]) -> str | None:
        if node.level:
            prefix = base[: len(base) - (node.level - 1)] if node.level > 1 else base
            return ".".join([*prefix, *((node.module or "").split(".") if node.module else [])])
        if (node.module or "").startswith("gtm_core"):
            return (node.module or "")[len("gtm_core") :].lstrip(".")
        return None


def _bound_names(target: ast.expr) -> set[str]:
    """Every name a binding target binds — ``x``, ``a, b``, ``(a, [b, c])`` alike."""
    return {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}


def _params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[list[str], list[str]]:
    args = fn.args
    return [a.arg for a in args.posonlyargs + args.args], [a.arg for a in args.kwonlyargs]


def _resolve_call(
    mod: _Module, node: ast.Call, known: dict[str, _Module]
) -> tuple[str, str] | None:
    """``(module, function)`` for a call whose target lives in the scanned package, else None."""
    func = node.func
    if isinstance(func, ast.Name):
        if func.id in mod.funcs:
            return (mod.key, func.id)
        return mod.names.get(func.id)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        alias = mod.aliases.get(func.value.id)
        if alias in known:
            return (alias, func.attr)
    return None


def _constant_mode(node: ast.Call, index: int) -> str | None:
    for arg in node.args[index : index + 1]:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    return None


def _written_paths(node: ast.Call) -> list[ast.expr]:
    """The expressions this call writes to — empty when it is not a filesystem write."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        if isinstance(func, ast.Name) and func.id == "open":
            mode = _constant_mode(node, 1)
            return node.args[:1] if mode and set(mode) - set("rbtU") else []
        return []
    qualified = (func.value.id, func.attr) if isinstance(func.value, ast.Name) else None
    if qualified in _QUALIFIED_WRITES:
        return [node.args[i] for i in _QUALIFIED_WRITES[qualified] if i < len(node.args)]
    if func.attr in _METHOD_WRITES:
        return [func.value]
    # `Path.replace(target)` takes one argument; `str.replace(a, b)` takes two. The arity is
    # the only thing that tells them apart without types, and a bare-name rule flagged every
    # marker-neutralising `text.replace(...)` in the tree.
    if func.attr == "replace" and len(node.args) == 1:
        return [func.value, node.args[0]]
    if func.attr == "open":
        mode = _constant_mode(node, 0)
        return [func.value] if mode and set(mode) - set("rbtU") else []
    return []


class _Scanner:
    """Taint over a whole scanned package, to a fixpoint."""

    def __init__(self, sources: dict[str, str]) -> None:
        self.mods = {key: _Module(key, src) for key, src in sources.items()}
        self.producers = set(_SEED_PRODUCERS)
        self.param_taint: dict[tuple[str, str], set[str]] = {}
        #: Per-element taint of what a function RETURNS, and of what iterating its return
        #: YIELDS, when either is tuple-shaped. Without these, `profile, reg = _load(args)`
        #: taints the profile string as well as the registry, `_safe_segment(profile)` becomes
        #: a "producer" of profile paths, and every content-root write in the tree lights up.
        self.return_shape: dict[tuple[str, str], list[bool]] = {}
        self.yield_shape: dict[tuple[str, str], list[bool]] = {}

    def _tainted(self, mod: _Module, expr: ast.expr | None, taint: set[str]) -> bool:
        if expr is None:
            return False
        if isinstance(expr, ast.Name):
            return expr.id in taint
        if isinstance(expr, ast.Attribute):
            # The `profiles_root` naming convention is the only thing that reaches a root
            # nobody resolved in-frame, and it has to hold for an ATTRIBUTE as well as a
            # parameter: `knowledge_meta.main` takes its root from
            # `PathConfig.from_env().profiles_root`, which no resolver call appears in.
            if expr.attr in _ROOT_PARAMS:
                return True
            return expr.attr in _PATH_STEPS and self._tainted(mod, expr.value, taint)
        if isinstance(expr, ast.Subscript):
            return self._tainted(mod, expr.value, taint)
        if isinstance(expr, ast.Call):
            return self._tainted_call(mod, expr, taint)
        return self._tainted_composite(mod, expr, taint)

    def _tainted_composite(self, mod: _Module, expr: ast.expr, taint: set[str]) -> bool:
        """Expressions tainted when one of their PARTS is — a join, a branch, a container
        literal, a comprehension. A container or comprehension of tainted paths counts as
        tainted, which is what lets a function that *builds* a list of profile paths become a
        producer, and what lets a `for` over that list taint its target."""
        if isinstance(expr, ast.BinOp):
            return self._tainted(mod, expr.left, taint) or self._tainted(mod, expr.right, taint)
        if isinstance(expr, ast.IfExp):
            return self._tainted(mod, expr.body, taint) or self._tainted(mod, expr.orelse, taint)
        if isinstance(expr, ast.Tuple | ast.List | ast.Set):
            return any(self._tainted(mod, e, taint) for e in expr.elts)
        if isinstance(expr, ast.ListComp | ast.SetComp | ast.GeneratorExp):
            return self._tainted(mod, expr.elt, self._comprehension_taint(mod, expr, taint))
        return False

    def _comprehension_taint(self, mod: _Module, expr: ast.expr, taint: set[str]) -> set[str]:
        """``taint`` plus each generator target bound from a tainted iterable."""
        widened = set(taint)
        for gen in expr.generators:
            widened |= self._bind(mod, gen.target, gen.iter, widened, iterating=True)
        return widened

    # --- tuple shape: which ELEMENT is the path ------------------------------------------

    def _tuple_shape(
        self, mod: _Module, expr: ast.expr | None, taint: set[str]
    ) -> list[bool] | None:
        """Per-element taint of a tuple-shaped expression, or None when the shape is unknown."""
        if isinstance(expr, ast.Tuple | ast.List):
            return [self._tainted(mod, e, taint) for e in expr.elts]
        if isinstance(expr, ast.Call):
            target = _resolve_call(mod, expr, self.mods)
            return self.return_shape.get(target) if target else None
        return None

    def _yield_shape(
        self, mod: _Module, expr: ast.expr | None, taint: set[str]
    ) -> list[bool] | None:
        """Per-element taint of what ITERATING ``expr`` yields, or None when unknown."""
        if isinstance(expr, ast.ListComp | ast.SetComp | ast.GeneratorExp):
            return self._tuple_shape(mod, expr.elt, self._comprehension_taint(mod, expr, taint))
        if isinstance(expr, ast.Call):
            func = expr.func
            if isinstance(func, ast.Name) and func.id in _PASSTHROUGH_CALLS and expr.args:
                return self._yield_shape(mod, expr.args[0], taint)
            target = _resolve_call(mod, expr, self.mods)
            return self.yield_shape.get(target) if target else None
        return None

    def _bind(
        self, mod: _Module, target: ast.expr, value: ast.expr, taint: set[str], *, iterating: bool
    ) -> set[str]:
        """The names ``target`` gains from binding ``value`` — element-wise when the shape is
        known, whole-value otherwise. ``iterating`` distinguishes ``x = v`` from ``for x in v``."""
        shape = (
            self._yield_shape(mod, value, taint)
            if iterating
            else self._tuple_shape(mod, value, taint)
        )
        if isinstance(target, ast.Tuple | ast.List) and shape is not None:
            if len(shape) == len(target.elts):
                names: set[str] = set()
                for element, is_tainted in zip(target.elts, shape, strict=True):
                    if is_tainted:
                        names |= _bound_names(element)
                return names
        return _bound_names(target) if self._tainted(mod, value, taint) else set()

    def _tainted_call(self, mod: _Module, expr: ast.Call, taint: set[str]) -> bool:
        if _resolve_call(mod, expr, self.mods) in self.producers:
            return True
        func = expr.func
        if isinstance(func, ast.Attribute) and func.attr in _PATH_STEPS | _PATH_ITER_STEPS:
            return self._tainted(mod, func.value, taint)
        if isinstance(func, ast.Name) and func.id in {"Path", *_PASSTHROUGH_CALLS} and expr.args:
            return self._tainted(mod, expr.args[0], taint)
        return False

    def _local_taint(self, mod: _Module, name: str, fn) -> set[str]:
        positional, keyword_only = _params(fn)
        taint = {p for p in positional + keyword_only if p in _ROOT_PARAMS}
        taint |= self.param_taint.get((mod.key, name), set())
        for _ in range(8):
            before = set(taint)
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign | ast.AnnAssign):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        taint |= self._bind(mod, target, node.value, taint, iterating=False)
                elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                    taint |= self._bind(
                        mod, node.optional_vars, node.context_expr, taint, iterating=False
                    )
                elif isinstance(node, ast.For | ast.AsyncFor):
                    # A `for` target is a binding exactly as `=` is. Leaving it out is what hid
                    # `knowledge_meta.seed`, whose whole write path is
                    # `for root, _ in managed_roots(...): for path in iter_managed_topics(root)`.
                    taint |= self._bind(mod, node.target, node.iter, taint, iterating=True)
                elif isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp):
                    taint |= self._comprehension_taint(mod, node, taint)
                elif isinstance(node, ast.Call):
                    taint |= self._accumulated(mod, node, taint)
            if taint == before:
                break
        return taint

    def _accumulated(self, mod: _Module, node: ast.Call, taint: set[str]) -> set[str]:
        """A collection that a tainted element was put INTO is itself tainted."""
        func = node.func
        if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
            return set()
        if func.attr in _ACCUMULATE_ELEMENT | _ACCUMULATE_CONTAINER and node.args:
            if self._tainted(mod, node.args[0], taint):
                return {func.value.id}
        return set()

    def _returned(self, fn) -> list[ast.expr]:
        return [n.value for n in ast.walk(fn) if isinstance(n, ast.Return) and n.value is not None]

    def _yielded(self, fn) -> list[ast.expr]:
        """What a GENERATOR hands out one at a time. `iter_managed_topics` is one, and reading
        only `Return` is why every managed-topic path it produced was clean."""
        return [
            n.value
            for n in ast.walk(fn)
            if isinstance(n, ast.Yield | ast.YieldFrom) and n.value is not None
        ]

    def _record_shapes(self, mod: _Module, name: str, fn, taint: set[str]) -> bool:
        """Remember what this function's return, and iterating it, hands out element-wise."""
        changed = False
        candidates = [(self.return_shape, self._tuple_shape, self._returned(fn))]
        candidates.append((self.yield_shape, self._yield_shape, self._returned(fn)))
        candidates.append((self.yield_shape, self._tuple_shape, self._yielded(fn)))
        for store, shape_of, values in candidates:
            for value in values:
                shape = shape_of(mod, value, taint)
                if shape and store.get((mod.key, name)) != shape:
                    store[(mod.key, name)] = shape
                    changed = True
        return changed

    def _visit(self, mod: _Module, name: str, fn) -> tuple[bool, list[tuple[str, int, str]], list]:
        taint = self._local_taint(mod, name, fn)
        self._record_shapes(mod, name, fn, taint)
        # A function annotated `-> str` hands back a NAME, not a path. `paths._safe_segment`
        # returns its own argument, so treating it as a path producer made every module that
        # validates a profile slug — content-root writers included — look like a profiles/
        # writer. Taint here is about paths; a declared string return ends it.
        returns_a_name = isinstance(fn.returns, ast.Name) and fn.returns.id in {
            "str",
            "bool",
            "int",
        }
        produces = not returns_a_name and any(
            self._tainted(mod, value, taint) for value in self._returned(fn) + self._yielded(fn)
        )
        writes: list[tuple[str, int, str]] = []
        calls = []
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            paths = _written_paths(node)
            if not paths and _resolve_call(mod, node, self.mods) in _HELPER_WRITES:
                paths = node.args[:1]
            if any(self._tainted(mod, p, taint) for p in paths):
                label = getattr(node.func, "attr", getattr(node.func, "id", "?"))
                writes.append((name, node.lineno, label))
            calls.append(
                (
                    _resolve_call(mod, node, self.mods),
                    [self._tainted(mod, a, taint) for a in node.args],
                    {kw.arg: self._tainted(mod, kw.value, taint) for kw in node.keywords},
                )
            )
        return produces, writes, calls

    def _spread(self, target: tuple[str, str], positional: list[bool], kw: dict) -> bool:
        mod = self.mods.get(target[0])
        fn = mod.funcs.get(target[1]) if mod else None
        if fn is None:
            return False
        names, kwonly = _params(fn)
        current = self.param_taint.setdefault(target, set())
        before = set(current)
        for i, is_tainted in enumerate(positional):
            if is_tainted and i < len(names):
                current.add(names[i])
        for key, is_tainted in kw.items():
            if is_tainted and key in names + kwonly:
                current.add(key)
        return current != before

    def run(self) -> dict[str, list[tuple[str, int, str]]]:
        for _ in range(10):
            changed = False
            before_shapes = (dict(self.return_shape), dict(self.yield_shape))
            for mod in self.mods.values():
                for name, fn in mod.funcs.items():
                    produces, _writes, calls = self._visit(mod, name, fn)
                    if produces and (mod.key, name) not in self.producers:
                        self.producers.add((mod.key, name))
                        changed = True
                    for target, positional, kw in calls:
                        if target and self._spread(target, positional, kw):
                            changed = True
            if before_shapes != (self.return_shape, self.yield_shape):
                changed = True
            if not changed:
                break

        findings: dict[str, list[tuple[str, int, str]]] = {}
        for mod in self.mods.values():
            for name, fn in mod.funcs.items():
                _produces, writes, _calls = self._visit(mod, name, fn)
                if writes:
                    findings.setdefault(mod.key, []).extend(writes)
        return findings


def _package_sources() -> dict[str, str]:
    return {_module_key(p): p.read_text(encoding="utf-8") for p in sorted(_PACKAGE.rglob("*.py"))}


def _tabled_writers() -> set[str]:
    """The RULES.md writer set, through THE parser that owns it — never a second copy."""
    path = Path(__file__).resolve().parent / "test_icp_check_is_read_only.py"
    spec = importlib.util.spec_from_file_location("_icp_contract_for_writer_scan", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._profiles_writers_from_rules()


@pytest.fixture(scope="module")
def findings() -> dict[str, list[tuple[str, int, str]]]:
    return _Scanner(_package_sources()).run()


def test_the_scan_saw_the_package_at_all(findings):
    """Guard the guard: an empty result would make every assertion below pass vacuously."""
    assert len(_package_sources()) > 100, "the source glob found almost nothing"
    assert findings, "the scanner found no profiles/ write anywhere — it has gone blind"


def test_every_write_under_profiles_is_named_in_the_rules_table(findings):
    """The missing direction: a write the table does not name is a writer nobody is enforcing."""
    tabled = _tabled_writers()
    allowed = tabled | _MECHANISM_ONLY
    untabled = {module: sorted(hits) for module, hits in findings.items() if module not in allowed}
    assert not untabled, (
        "these modules write to a path derived from profiles/ but are NOT in the writer table "
        f"in docs/RULES.md: {untabled}. Add a row naming the real scope of the write, or, if "
        "the module only provides the mechanism and its callers own the guarantee, add it to "
        "_MECHANISM_ONLY with the reason."
    )


def test_every_tabled_writer_is_still_reachable_by_the_scan(findings):
    """The reverse: a row whose module the scanner can no longer see is a row gone stale.

    xfail-free on purpose — the four flat writers, both `messaging` ones and `voc.registry` are
    all reachable today. A writer that moves out of the scanner's sight (a string-literal path, a
    dynamic dispatch) should fail here and be dealt with, not quietly stop being checked.
    """
    tabled = _tabled_writers()
    if not (_REPO / "scripts" / "oss-export.sh").is_file():
        # The public cut: it has no export script, and it withholds some tabled writers (e.g.
        # voc.registry) while shipping the RULES.md table that names them. A module that is not
        # there cannot be stale. In the private tree this branch never runs.
        tabled = {
            m
            for m in tabled
            if (_REPO / "gtm_core" / Path(*m.split("."))).with_suffix(".py").is_file()
        }
    missing = sorted(tabled - set(findings))
    assert not missing, (
        f"RULES.md names {missing} as writer(s) of profiles/, but the scan sees no tainted "
        "write there — either the writer moved out of the detector's reach (see the blind-spot "
        "list in this module's docstring) or the row is stale"
    )


# --- §R12 controls: the detector must be able to fail ------------------------------------

_FAKE_PATHS = """
from pathlib import Path


def resolve_knowledge_file(profiles_root, profile, name):
    return Path(profiles_root) / profile / "knowledge" / name


def resolve_content_root():
    return Path("content")
"""


def _scan_snippet(**modules: str) -> dict[str, list[tuple[str, int, str]]]:
    return _Scanner({"paths": _FAKE_PATHS, **modules}).run()


def test_the_detector_finds_a_planted_writer():
    """A green run must not be achievable by detecting nothing (§R12/§R18).

    Three shapes, because the real writers use all three: a resolver call written straight into
    a local, a path returned by a module-local helper, and a bare `path` parameter fed by
    another module's call site.
    """
    direct = _scan_snippet(
        planted="""
from .paths import resolve_knowledge_file


def sneak(profile):
    target = resolve_knowledge_file("profiles", profile, "voice.md")
    target.write_text("owned", encoding="utf-8")
"""
    )
    assert "planted" in direct, "a resolver call assigned to a local must be caught"

    via_helper = _scan_snippet(
        planted="""
from .paths import resolve_knowledge_file


def where(profile):
    return resolve_knowledge_file("profiles", profile, "voice.md")


def sneak(profile):
    where(profile).write_text("owned", encoding="utf-8")
"""
    )
    assert "planted" in via_helper, "a path from a module-local helper must be caught"

    across_modules = _scan_snippet(
        writer="""
def save(path, text):
    path.write_text(text, encoding="utf-8")
""",
        caller="""
from .paths import resolve_knowledge_file

from . import writer


def sneak(profile):
    writer.save(resolve_knowledge_file("profiles", profile, "voice.md"), "owned")
""",
    )
    assert "writer" in across_modules, "a tainted argument must taint the callee's parameter"


def test_the_detector_follows_taint_into_a_loop_and_a_comprehension():
    """A binding is a binding whether ``=`` or ``for`` made it (§R12).

    Until 2026-09-24 ``_local_taint`` propagated through ``Assign``/``AnnAssign``/``withitem``
    only, so a path bound by a ``for`` target or a comprehension target was clean. That is the
    exact shape ``knowledge_meta.seed`` uses — ``for root, prefix in managed_roots(...)`` over a
    list comprehension of profile roots — and it made an eighth writer invisible while the
    detector reported green. Four shapes: a plain ``for``, a tuple-unpacking ``for``, a
    comprehension that PRODUCES the tainted list, and a comprehension target that is then
    written.
    """
    plain_for = _scan_snippet(
        planted="""
from .paths import resolve_profiles_root


def sneak(profile):
    for target in (resolve_profiles_root() / profile).iterdir():
        target.write_text("owned", encoding="utf-8")
"""
    )
    assert "planted" in plain_for, "a `for` target bound from a tainted iterable must be caught"

    unpacking_for = _scan_snippet(
        planted="""
from .paths import resolve_profiles_root


def roots(profile):
    return [(resolve_profiles_root() / profile / sub, sub) for sub in ("knowledge", "products")]


def sneak(profile):
    for root, _prefix in roots(profile):
        root.write_text("owned", encoding="utf-8")
"""
    )
    assert "planted" in unpacking_for, (
        "a tuple-unpacking `for` over a comprehension-built list of tainted paths must be caught"
    )

    comprehension_target = _scan_snippet(
        planted="""
from .paths import resolve_profiles_root


def sneak(profile):
    base = resolve_profiles_root() / profile
    return [child.write_text("owned", encoding="utf-8") for child in base.iterdir()]
"""
    )
    assert "planted" in comprehension_target, (
        "a comprehension target bound from a tainted iterable must be caught"
    )

    returned_comprehension = _scan_snippet(
        planted="""
from .paths import resolve_profiles_root


def roots(profile):
    return [resolve_profiles_root() / profile / sub for sub in ("knowledge", "products")]


def sneak(profile):
    for root in roots(profile):
        root.write_text("owned", encoding="utf-8")
"""
    )
    assert "planted" in returned_comprehension, (
        "a comprehension that BUILDS tainted paths must make its function a producer"
    )


def test_the_detector_follows_a_generator_a_passthrough_and_an_accumulator():
    """The three remaining shapes `knowledge_meta` needed, each on its own (§R12).

    `iter_managed_topics` is `out = []` / `for p in sorted(root.rglob("*.md"))` /
    `out.append(p)` / `return out`. Reading only `return` misses a generator; ignoring
    `sorted(...)` loses the iterable; ignoring `.append` loses the accumulator. Any one of the
    three left out puts the eighth writer back out of sight.
    """
    generator = _scan_snippet(
        planted="""
from .paths import resolve_profiles_root


def topics(profile):
    for child in (resolve_profiles_root() / profile).iterdir():
        yield child


def sneak(profile):
    for path in topics(profile):
        path.write_text("owned", encoding="utf-8")
"""
    )
    assert "planted" in generator, "a generator that yields tainted paths must be a producer"

    passthrough = _scan_snippet(
        planted="""
from .paths import resolve_profiles_root


def sneak(profile):
    for path in sorted((resolve_profiles_root() / profile).rglob("*.md")):
        path.write_text("owned", encoding="utf-8")
"""
    )
    assert "planted" in passthrough, "`sorted(...)` must not launder a tainted iterable"

    accumulator = _scan_snippet(
        planted="""
from .paths import resolve_profiles_root


def topics(profile):
    out = []
    for child in (resolve_profiles_root() / profile).rglob("*.md"):
        out.append(child)
    return out


def sneak(profile):
    for path in topics(profile):
        path.write_text("owned", encoding="utf-8")
"""
    )
    assert "planted" in accumulator, "a list built by `.append` of tainted paths must be tainted"


def test_the_detector_unpacks_a_tuple_element_wise():
    """Precision control for the element-wise rule, both directions.

    Whole-tuple taint is not a safe over-approximation here: it makes the profile SLUG in
    `profile, reg = _load(args)` a path, which makes `paths._safe_segment` a path producer,
    which puts every content-root writer in the tree on the findings list. So a `(path, str)`
    pair must taint the path half and only the path half.
    """
    caught = _scan_snippet(
        planted="""
from .paths import resolve_profiles_root


def roots(profile):
    return [(resolve_profiles_root() / profile / sub, sub) for sub in ("knowledge", "products")]


def sneak(profile):
    for root, _label in roots(profile):
        root.write_text("owned", encoding="utf-8")
"""
    )
    assert "planted" in caught, "the PATH half of an unpacked pair must stay tainted"

    clean = _scan_snippet(
        planted="""
from .paths import resolve_content_root, resolve_profiles_root


def pair(profile):
    return resolve_profiles_root() / profile, profile


def sneak(profile):
    _root, label = pair(profile)
    out = resolve_content_root() / label / "report.md"
    out.write_text("fine", encoding="utf-8")
"""
    )
    assert not clean, f"the STRING half of an unpacked pair must not be a path: {clean}"


def test_the_detector_ignores_a_write_under_the_content_root():
    """Precision control. `content/<tenant>/` is where deliverables belong; flagging those
    would put twenty modules in the table and train the next reader to add rows without
    reading them."""
    clean = _scan_snippet(
        planted="""
from .paths import resolve_content_root


def fine(profile):
    out = resolve_content_root() / profile / "report.md"
    out.write_text("fine", encoding="utf-8")
"""
    )
    assert not clean, f"a content-root write must not be a finding: {clean}"


def test_the_detector_ignores_a_string_replace():
    """The `replace` arity rule, both directions — `str.replace` is not `Path.replace`."""
    clean = _scan_snippet(
        planted="""
from .paths import resolve_knowledge_file


def fine(profile):
    target = resolve_knowledge_file("profiles", profile, "voice.md")
    return str(target).replace("a", "b")
"""
    )
    assert not clean, f"a two-argument str.replace must not be a finding: {clean}"

    caught = _scan_snippet(
        planted="""
from .paths import resolve_knowledge_file


def sneak(profile, tmp):
    target = resolve_knowledge_file("profiles", profile, "voice.md")
    tmp.replace(target)
"""
    )
    assert "planted" in caught, "a one-argument Path.replace onto a tainted target must be caught"
