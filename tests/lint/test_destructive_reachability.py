"""The gate in :mod:`tests.lint.destructive_reachability`, and proof that it can see the incident.

A reachability gate that reports "clean" is worth nothing until it has been shown to go red on the
code that caused the outage (§R18). The control here is not a mock: it is the real
``prospects_consolidate/consolidate.py`` as it stood in commit ``b0629da4``, read out of git.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.lint.destructive_reachability import (
    ASSERTED_CLEAN,
    DESTRUCTIVE_MODULES,
    edgeless_assertions,
    import_graph,
    reaches,
    refs_from_source,
    skill_cited_modules,
    uncited_assertions,
    violations,
)

#: The merge that wired the 7-day purge into every consolidate.
INCIDENT = "b0629da4"
INCIDENT_FILE = "gtm_core/prospects_consolidate/consolidate.py"


def test_no_skill_cited_module_reaches_a_destructive_one() -> None:
    bad = violations()
    assert not bad, "\n".join(f"{r}: {' -> '.join(c)}" for r, c in bad)


def test_the_roots_are_really_derived_from_the_skills() -> None:
    """Instrument check: an extractor that silently finds nothing passes everything."""
    roots = skill_cited_modules()
    assert len(roots) >= 40, f"only {len(roots)} skill-cited modules — the extractor is broken"
    assert "gtm_core.prospects_consolidate" in roots


def test_the_graph_sees_an_import_written_inside_a_function() -> None:
    """The incident's import was function-local, which is what a module-level scan would miss."""
    src = "def consolidate():\n    from ..retention_sweep import sweep_stale_pii\n"
    refs = refs_from_source(src, "gtm_core.prospects_consolidate.consolidate")
    assert "gtm_core.retention_sweep" in refs


def test_the_gate_goes_red_on_the_real_incident_commit() -> None:
    """The negative control, from git rather than from imagination."""
    proc = subprocess.run(
        ["git", "show", f"{INCIDENT}:{INCIDENT_FILE}"],
        capture_output=True,
        text=True,
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[2]),
    )
    if proc.returncode != 0:
        pytest.skip(f"{INCIDENT} not present in this checkout")
    here = INCIDENT_FILE.removesuffix(".py").replace("/", ".")
    assert "gtm_core.retention_sweep" in refs_from_source(proc.stdout, here), (
        "the incident's own source no longer trips the detector — the detector is broken"
    )
    graph = dict(import_graph())
    graph[here] = frozenset(refs_from_source(proc.stdout, here))
    graph["gtm_core.prospects_consolidate"] = frozenset({here})
    chain = reaches(graph, "gtm_core.prospects_consolidate", DESTRUCTIVE_MODULES)
    assert chain and chain[-1] == "gtm_core.retention_sweep"


def test_a_clean_graph_reports_no_chain() -> None:
    """The other half of discrimination: it must also be capable of saying no."""
    graph = {"a": frozenset({"b"}), "b": frozenset()}
    assert reaches(graph, "a", DESTRUCTIVE_MODULES) is None


def test_the_read_shaped_modules_are_still_cited() -> None:
    """The instrument check for ASSERTED_CLEAN. A module no skill cites is not a root, so the
    clean-ness assertion below would hold trivially and prove nothing."""
    uncited = uncited_assertions()
    assert not uncited, (
        f"asserted-clean but no longer cited by any skill: {uncited}. Either restore the "
        "citation or drop the name from ASSERTED_CLEAN — do not leave a check that cannot fail."
    )


def test_the_read_shaped_modules_reach_nothing_destructive() -> None:
    graph = import_graph()
    for module in sorted(ASSERTED_CLEAN):
        chain = reaches(graph, module, DESTRUCTIVE_MODULES)
        assert chain is None, (
            f"{module} is named like a read but reaches {' -> '.join(chain or [])}"
        )


def test_the_clean_assertion_can_go_red() -> None:
    """Negative control. Without it, a `reaches()` that always returned None would leave the
    test above green over any module at all."""
    poisoned = {
        "gtm_core.scorecard": frozenset({"gtm_core.scorecard.score"}),
        "gtm_core.scorecard.score": frozenset({"gtm_core.retention_sweep"}),
    }
    chain = reaches(poisoned, "gtm_core.scorecard", DESTRUCTIVE_MODULES)
    assert chain == ["gtm_core.scorecard", "gtm_core.scorecard.score", "gtm_core.retention_sweep"]


def test_the_read_shaped_modules_have_real_outgoing_edges() -> None:
    """The second instrument check. A root whose imports resolve to names absent from the graph
    has no edges at all, so "reaches nothing destructive" is true of it the way it is true of an
    empty file. `gtm_core.scorecard` was in exactly that state until the package-`__init__`
    off-by-one in `_resolve` was fixed."""
    edgeless = edgeless_assertions()
    assert not edgeless, (
        f"asserted-clean but has no resolvable imports: {edgeless}. The verdict is a property of "
        "the import graph, not of the code — fix the resolver before trusting it."
    )


def test_a_package_init_resolves_its_own_relative_imports() -> None:
    """The regression. `module_name()` already pops `__init__`, so a package's own `__init__.py`
    is AT its package and must not climb another level."""
    refs = refs_from_source("from .score import score_row\n", "gtm_core.scorecard", is_package=True)
    assert "gtm_core.scorecard.score" in refs
    assert "gtm_core.score" not in refs, "climbed one level too far — the 2026-09-22 defect"


def test_a_plain_module_still_climbs_one_level() -> None:
    """The other half: the fix must not stop ordinary modules resolving their siblings."""
    refs = refs_from_source("from .score import score_row\n", "gtm_core.scorecard.cli")
    assert "gtm_core.scorecard.score" in refs
