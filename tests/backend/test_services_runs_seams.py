"""Structural seams of the run-lifecycle service package (PRD 2026-09-01 §6.2 V1, Phase 1a).

Three properties the pure-motion split must hold, checked mechanically rather than by
reading the diff:

* every submodule imports (the import-walk — kills §6.1 rows 1 and 6 locally);
* each of the router's eight in-process state objects is DEFINED exactly once, in
  ``state.py``, and every other module that names it holds the very same object (§6.1
  row 3 — the split-brain bug is a second definition, and it is silent);
* the shared test harness patches ``workspace_scope`` in every service module that
  binds it, so a new module can never quietly run against a real pool while the fixture
  believes it is faked (§6.1 row 4, second-order form).
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import backend.services.runs as pkg
from backend.routers import runs as runs_router
from backend.services.runs import state

STATE_OBJECTS = (
    "_gate_events",
    "_gate_decisions",
    "_cancelled_runs",
    "_run_subscribers",
    "_workspace_stream_count",
    "_workspace_runs",
    "_state_lock",
    "_background_tasks",
)

PKG_DIR = Path(pkg.__file__).resolve().parent


def _submodules() -> list[str]:
    return sorted(m.name for m in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."))


def test_every_submodule_imports():
    names = _submodules()
    assert names, "the service package is empty"
    for name in names:
        importlib.import_module(name)


def test_each_state_object_is_defined_once_in_state_py():
    """A module-level assignment to one of the eight names anywhere but state.py is the
    split-brain bug: two registries, no exception, a run that hangs at its gate."""
    definers: dict[str, list[str]] = {n: [] for n in STATE_OBJECTS}
    for path in sorted(PKG_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            targets = []
            if isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
            for t in targets:
                if t in definers:
                    definers[t].append(path.name)
    assert definers == {n: ["state.py"] for n in STATE_OBJECTS}, definers


def test_router_and_every_service_module_share_the_state_objects():
    modules = [importlib.import_module(n) for n in _submodules()] + [runs_router]
    for name in STATE_OBJECTS:
        canonical = getattr(state, name)
        for mod in modules:
            if hasattr(mod, name):
                assert getattr(mod, name) is canonical, f"{mod.__name__}.{name} is a copy"


def test_the_harness_patches_every_binding_of_every_faked_collaborator():
    """The harness fakes three collaborators by patching them where the lifecycle code
    binds them. Each list must cover EVERY module that binds its name, exactly:

    * a missing module runs its real query/spend-check/push against the real thing while
      the fixture believes it is faked (§6.1 row 4 in its second-order form);
    * a stale module is an `AttributeError` at patch time — noisy, but it means the tuple
      has drifted from the code and the next split will trust it.

    Both directions are checked, so a refactor that moves a binding fails HERE with the
    exact module to add or drop, rather than in forty scattered suites."""
    from tests.backend import _protocol1

    # + backend.services.integrations: not a runs module, but every pack run calls its
    # get_workspace_credentials, which binds workspace_scope (the BYOK RLS fix).
    mods = [importlib.import_module(n) for n in _submodules()] + [
        runs_router,
        importlib.import_module("backend.services.integrations"),
    ]
    for attr, tuple_name in (
        ("workspace_scope", "SCOPE_MODULES"),
        ("acheck_budget", "BUDGET_MODULES"),
        ("send_gate_push", "PUSH_MODULES"),
        ("send_run_done_push", "DONE_PUSH_MODULES"),
    ):
        binders = {m.__name__ for m in mods if hasattr(m, attr)}
        patched = {m.__name__ for m in getattr(_protocol1, tuple_name)}
        assert binders == patched, {
            "attr": attr,
            "tuple": tuple_name,
            "unpatched": sorted(binders - patched),
            "stale": sorted(patched - binders),
        }
