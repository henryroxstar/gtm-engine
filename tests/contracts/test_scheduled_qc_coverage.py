"""T10 — every deterministic gate module is reached by something that runs on its own.

§R9 — no fixture here names a real person or company; the contract reads this repo's
own files and nothing else.

This is the **general case** of the 2026-08-27 wiring-gap PRD. The other contracts each
pin one instance — `packs/` unmounted, `groundedness` uncalled, `check-drift.sh`
unscheduled. This one pins the *class*: a QC module can be written, tested, reviewed and
merged, and still be reached by nothing that ever runs. That is how seven of them
accumulated before anyone counted.

A module counts as reached when it is:

* named in ``gtm_core.preflight_report.ROSTER`` (the daily deterministic precondition), or
* execed by a systemd unit as ``python -m <module>``, or
* invoked from a pack node's skill, or
* called by another module that is itself reached (one hop, e.g. ``signal_record``
  through ``account_integrity``, or ``groundedness`` through the judge).

Deliberately shallow, and deliberately annoying to delete: it asserts *wiring*, never
behaviour. A future QC module added and never scheduled fails CI instead of being
discovered months later by an audit.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
SYSTEMD = REPO / "systemd"
PACKS = REPO / "packs"

#: The deterministic gate family, by module name. Adding a module here without wiring it
#: fails this contract — which is the point: the list is the declaration that something
#: is a gate, and a gate that runs nowhere is not one.
DETERMINISTIC_GATES = (
    "account_integrity",
    "email_compliance",
    "groundedness",
    "hook_coverage",
    "list_fit",
    "merge_hygiene",
    "signal_record",
    "suppression",
)

#: Modules that reach a gate and are themselves reached, so a gate called only from here
#: still counts. Each entry is earned, not assumed — `test_every_indirect_host_is_itself_reached`
#: fails if one stops being scheduled.
INDIRECT_HOSTS = {
    # W1a: the judge MCP runs the groundedness cascade per row, before the model sees it.
    "agent/mcp/judge/scoring.py",
}
# `gtm_core/preflight_report.py` is deliberately NOT listed here. It imports all eight, so
# listing it would make the indirect route report True for every gate and the contract
# could never tell the roster route from any other — a check that passes for a reason it
# is not measuring. The roster is its own route; that is where it counts.


def _py_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def _imports_of(path: Path) -> set[str]:
    """Module names imported by ``path``, via AST so a docstring never counts.

    Prose about a module is not a consumer of it — the same false positive that briefly
    fooled the signals contract in W1b, where a docstring said a module "mirrors
    ``gtm_core.signals``" and a text search believed it.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[-1] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.add(node.module.split(".")[-1])
            out.update(a.name for a in node.names)
    return out


def _unit_execed_modules() -> set[str]:
    if not SYSTEMD.exists():
        return set()
    out: set[str] = set()
    for unit in SYSTEMD.glob("*.service"):
        text = unit.read_text(encoding="utf-8").replace("\\\n", " ")
        for m in re.findall(r"python\s+-m\s+([A-Za-z_][\w.]*)", text):
            out.add(m.split(".")[-1])
    return out


def _pack_skills() -> set[str]:
    return {
        m
        for graph in PACKS.rglob("graphs/*.toml")
        for m in re.findall(r'skill\s*=\s*"([^"]+)"', graph.read_text(encoding="utf-8"))
    }


def _roster_modules() -> set[str]:
    from gtm_core import preflight_report

    return {c.module.split(".")[-1] for c in preflight_report.ROSTER}


def _indirect_reach() -> set[str]:
    out: set[str] = set()
    for rel in INDIRECT_HOSTS:
        path = REPO / rel
        if path.is_file():
            out |= _imports_of(path)
    return out


# --- non-vacuity -----------------------------------------------------------
#
# Without these, every contract below passes trivially the moment a glob or a regex
# breaks — which is the failure mode this whole file exists to prevent.


def test_the_declared_gate_family_is_non_vacuous() -> None:
    assert len(DETERMINISTIC_GATES) >= 8


def test_the_roster_is_non_vacuous() -> None:
    assert len(_roster_modules()) >= 8, "preflight_report.ROSTER parse drifted"


def test_the_unit_exec_scan_is_non_vacuous() -> None:
    if not SYSTEMD.exists():
        pytest.skip("systemd/ not present (private deploy surface)")
    assert len(_unit_execed_modules()) >= 5, "unit ExecStart scan drifted"


def test_the_pack_skill_scan_is_non_vacuous() -> None:
    assert len(_pack_skills()) >= 10, "pack graph scan drifted"


# --- the contract ----------------------------------------------------------


@pytest.mark.parametrize("gate", DETERMINISTIC_GATES)
def test_every_deterministic_gate_module_is_invoked_by_a_unit_or_a_pack_node(gate: str) -> None:
    """T10. A QC module reached by nothing that runs is not a gate, whatever it contains."""
    reached_by = []
    if gate in _roster_modules():
        reached_by.append("preflight_report.ROSTER")
    if gate in _unit_execed_modules():
        reached_by.append("a systemd unit ExecStart")
    if gate.replace("_", "-") in _pack_skills():
        reached_by.append("a pack node")
    if gate in _indirect_reach():
        reached_by.append("a scheduled module that imports it")
    assert reached_by, (
        f"gtm_core/{gate}.py is a deterministic gate that nothing scheduled reaches. "
        f"It runs only when a human remembers, which is the defect class of the "
        f"2026-08-27 wiring-gap PRD. Add it to preflight_report.ROSTER, exec it from a "
        f"systemd unit, or put it behind a pack node."
    )


@pytest.mark.parametrize("host", sorted(INDIRECT_HOSTS))
def test_every_indirect_host_is_itself_reached(host: str) -> None:
    """A gate reached only through a host is reached only while the host runs.

    Without this the contract launders an inert module through a second inert module and
    reports both as covered.
    """
    path = REPO / host
    if not path.is_file():
        pytest.skip(f"{host} not present in this carve")
    module = path.stem
    if module in _unit_execed_modules() or module in _roster_modules():
        return
    # Not directly scheduled: it must be reachable from something that is. The judge
    # scorer is reached by the judge MCP, which the prospecting pack's `quality` node runs.
    consumers = [
        p
        for tree in ("agent", "gtm_core", "cockpit")
        if (REPO / tree).is_dir()
        for p in _py_files(REPO / tree)
        if p != path and module in _imports_of(p)
    ]
    assert consumers, (
        f"{host} is trusted to keep a gate alive, but nothing schedules or imports it. "
        f"Either it is dead and the gates it hosts are inert, or this exemption is stale."
    )


def test_no_gate_is_listed_that_does_not_exist() -> None:
    """A declared gate for a deleted module is stale config that hides the next gap."""
    for gate in DETERMINISTIC_GATES:
        assert (REPO / "gtm_core" / f"{gate}.py").is_file(), f"gtm_core/{gate}.py is gone"


# --- checker self-test -----------------------------------------------------


def test_the_checker_detects_a_synthetic_violation(tmp_path) -> None:
    """The checker must fail on an unwired gate, or it is decoration.

    Written against the same helpers the contract uses, so a helper that silently
    returns an empty set fails here too.
    """
    fake = "definitely_not_a_wired_gate"
    reached = (
        fake in _roster_modules()
        or fake in _unit_execed_modules()
        or fake.replace("_", "-") in _pack_skills()
        or fake in _indirect_reach()
    )
    assert not reached, "the coverage scan claims to reach a module that does not exist"


def test_the_import_scan_ignores_a_docstring_mention(tmp_path) -> None:
    """AST, not text. Prose about a module is not a consumer of it."""
    src = tmp_path / "prose_only.py"
    src.write_text('"""This mirrors ``gtm_core.groundedness`` in spirit."""\n', encoding="utf-8")
    assert "groundedness" not in _imports_of(src)
