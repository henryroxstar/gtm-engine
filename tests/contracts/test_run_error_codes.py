"""M-07: every run failure is written with a code from the closed set, checked at the call.

``_fail_run`` takes ``error_code`` as a required keyword and refuses a value outside
``RUN_ERROR_CODES`` — but only on a path that executes, and a failure path is the one a suite
exercises least. This reads every call statically:

* every ``_fail_run(...)`` under ``backend/`` passes ``error_code=``;
* the value is a string literal in the set, a conditional between such literals, or a local
  name only ever assigned those — the code is fixed where the failure happens, never derived
  from the prose passed beside it (a client could then just as well match the prose);
* the one other shape, ``<failure>.code`` from a ``RunFailure`` its producer built, appears only
  at the pinned sites, and every ``RunFailure(...)`` names a literal code (one pinned lookup
  aside, whose table is checked here too).

The ``done`` frame's schema lists the same set, so the published contract names every code.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from backend.services.runs.persistence import _PUBLISH_REFUSAL_CODES, RUN_ERROR_CODES

REPO = Path(__file__).resolve().parents[2]

#: (file, function) where ``_fail_run`` receives a code carried in a ``RunFailure``.
CARRIED_FAIL_RUN_SITES = {
    ("backend/services/runs/pack_executor.py", "_execute_pack_run"),
    ("backend/services/runs/reconcile.py", "reconcile_gates"),
}
#: (file, function) where a ``RunFailure`` code is looked up rather than written literally.
LOOKED_UP_FAILURE_SITES = {("backend/services/runs/persistence.py", "publish_refusal")}
#: Every module that fails runs today — the scan must find a call in each.
FAILING_MODULES = {
    "backend/services/runs/executor.py",
    "backend/services/runs/fake.py",
    "backend/services/runs/lifecycle.py",
    "backend/services/runs/pack_executor.py",
    "backend/services/runs/queue.py",
    "backend/services/runs/reconcile.py",
}


def _codes(node: ast.expr, assigns: dict[str, list[ast.expr]]) -> set[str] | None:
    """Every code ``node`` can evaluate to, or None when that is not fixed in the source."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        body, orelse = _codes(node.body, assigns), _codes(node.orelse, assigns)
        return None if body is None or orelse is None else body | orelse
    if isinstance(node, ast.Name) and assigns.get(node.id):
        found: set[str] = set()
        for value in assigns[node.id]:
            codes = _codes(value, {})
            if codes is None:
                return None
            found |= codes
        return found
    return None


def _carried(node: ast.expr) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "code"


class _Scan(ast.NodeVisitor):
    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.stack: list[tuple[str, dict[str, list[ast.expr]]]] = []
        self.findings: list[str] = []
        self.fail_run_modules: set[str] = set()
        self.carried: set[tuple[str, str]] = set()
        self.looked_up: set[tuple[str, str]] = set()

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        assigns: dict[str, list[ast.expr]] = {}
        for inner in ast.walk(node):
            if isinstance(inner, ast.Assign):
                for target in inner.targets:
                    if isinstance(target, ast.Name):
                        assigns.setdefault(target.id, []).append(inner.value)
        self.stack.append((node.name, assigns))
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "_fail_run":
            self._fail_run(node)
        elif name == "RunFailure":
            self._run_failure(node)
        self.generic_visit(node)

    def _where(self, node: ast.Call) -> tuple[str, str, dict[str, list[ast.expr]]]:
        function, assigns = self.stack[-1] if self.stack else ("<module>", {})
        return f"{self.rel}:{node.lineno} in {function}", function, assigns

    def _check(self, where: str, value: ast.expr, assigns) -> None:
        codes = _codes(value, assigns)
        if codes is None:
            self.findings.append(f"{where}: code is not a literal from the closed set")
        elif codes - RUN_ERROR_CODES:
            self.findings.append(f"{where}: unknown code(s) {sorted(codes - RUN_ERROR_CODES)}")

    def _fail_run(self, node: ast.Call) -> None:
        where, function, assigns = self._where(node)
        self.fail_run_modules.add(self.rel)
        value = next((k.value for k in node.keywords if k.arg == "error_code"), None)
        if value is None:
            self.findings.append(f"{where}: _fail_run without error_code=")
        elif _carried(value):
            self.carried.add((self.rel, function))
        else:
            self._check(where, value, assigns)

    def _run_failure(self, node: ast.Call) -> None:
        where, function, assigns = self._where(node)
        code = (
            node.args[0]
            if node.args
            else next((k.value for k in node.keywords if k.arg == "code"), None)
        )
        if code is None:
            self.findings.append(f"{where}: RunFailure without a code")
        elif _codes(code, assigns) is None and (self.rel, function) in LOOKED_UP_FAILURE_SITES:
            self.looked_up.add((self.rel, function))
        else:
            self._check(where, code, assigns)


def _scan_source(source: str, rel: str) -> _Scan:
    scan = _Scan(rel)
    scan.visit(ast.parse(source))
    return scan


def _scan_backend() -> _Scan:
    total = _Scan("")
    for path in sorted((REPO / "backend").rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        scan = _scan_source(path.read_text(encoding="utf-8"), rel)
        total.findings += scan.findings
        total.fail_run_modules |= scan.fail_run_modules
        total.carried |= scan.carried
        total.looked_up |= scan.looked_up
    return total


def test_every_run_failure_in_the_backend_names_a_code_from_the_closed_set():
    scan = _scan_backend()
    assert scan.findings == []
    assert scan.fail_run_modules == FAILING_MODULES


def test_a_code_carried_from_its_source_is_read_only_at_the_pinned_sites():
    scan = _scan_backend()
    assert scan.carried == CARRIED_FAIL_RUN_SITES
    assert scan.looked_up == LOOKED_UP_FAILURE_SITES
    assert set(_PUBLISH_REFUSAL_CODES.values()) <= RUN_ERROR_CODES


def test_the_scan_flags_every_shape_it_claims_to():
    """§R18: a scan that cannot tell a bad call from a good one proves nothing."""
    source = """
async def good(pool, refused, failure):
    await _fail_run(pool, "w", "r", "gate timeout", error_code="gate_timeout")
    code = "cost_cap_reached" if refused else "node_failed"
    await _fail_run(pool, "w", "r", "x", error_code=code)
    await _fail_run(pool, "w", "r", "x", error_code="internal_error" if refused else "node_failed")
    return RunFailure("draft_invalid", "x")

async def missing(pool):
    await _fail_run(pool, "w", "r", "monthly cost cap reached")

async def unknown(pool):
    await _fail_run(pool, "w", "r", "x", error_code="cap_reached")

async def from_prose(pool, error):
    await _fail_run(pool, "w", "r", error, error_code=error.split()[0])

async def unpinned(pool, failure):
    await _fail_run(pool, "w", "r", failure.error, error_code=failure.code)

def bad_failure():
    return RunFailure("nope", "x")
"""
    scan = _scan_source(source, "backend/example.py")
    assert scan.findings == [
        "backend/example.py:10 in missing: _fail_run without error_code=",
        "backend/example.py:13 in unknown: unknown code(s) ['cap_reached']",
        "backend/example.py:16 in from_prose: code is not a literal from the closed set",
        "backend/example.py:22 in bad_failure: unknown code(s) ['nope']",
    ]
    assert scan.carried == {("backend/example.py", "unpinned")}
    assert not scan.carried & CARRIED_FAIL_RUN_SITES


def test_the_done_frame_schema_lists_exactly_the_closed_set():
    schema = json.loads((REPO / "schemas" / "run-event.schema.json").read_text(encoding="utf-8"))
    done = next(b for b in schema["oneOf"] if b["properties"]["event"].get("const") == "done")
    description = done["properties"]["data"]["properties"]["error_code"]["description"]
    listed = re.search(r"Current values: ([a-z_| ]+)\.", description)
    assert listed is not None, description
    assert {code.strip() for code in listed.group(1).split("|")} == RUN_ERROR_CODES
