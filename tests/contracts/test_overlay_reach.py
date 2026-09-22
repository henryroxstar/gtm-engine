"""``--overlay`` must reach every reader of an overlayable file — enforced, not enumerated.

An overlay replaces targeting files for the length of ONE run so a different ICP or hook matrix
can be tried without touching the live one. ``experiments.OVERLAYABLE`` admits eight files; the
kill switch and the admission gate are already enforced elsewhere. What was never enforced is
the other half: that the modules which actually RESOLVE those files can be told about the
overlay at all.

Measured 2026-09-21, the answer splits cleanly along the two halves of the allowlist:

* **The targeting half is wired.** ``icp-personas.md``, ``icp-scoring.toml`` and
  ``role-vocabulary.toml`` all resolve through functions that accept ``overlay``.
* **The messaging half is not.** ``hook-matrix.md``, ``hooks.toml`` and ``premise-vocab.toml``
  are on the allowlist and no reader of them accepts an ``overlay`` argument.

So an ICP experiment today varies **who** you target and not **what you say to them** — you can
swap the rubric and not the hook bank, which makes a messaging experiment unrepresentable even
though the allowlist claims to admit one.

**Why this ships as an enforced contract with a recorded xfail rather than as a fix.**
Threading ``overlay`` through the messaging side is a feature — "let me experiment on the copy,
not just the audience" — touching nine functions across three modules plus two large skills. It
deserves its own decision about whether that experiment is wanted, rather than having a test
decide it. What this file buys today is that the gap is named, bounded and impossible to
forget: the xfail is ``strict``, so the day someone wires a module the test FAILS until they
remove it from the list, and a new overlayable file with no overlay-aware reader fails
immediately rather than joining the gap silently.

Follow-up tracked in PENDING.md.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from gtm_core import experiments

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: filename -> the modules that resolve it, outside experiments.py itself (which only declares
#: the allowlist). Derived by grepping for the literal filename and kept here so the test names
#: what it checked; a file with NO resolver at all is handled separately below.
_RESOLVERS: dict[str, tuple[str, ...]] = {
    "icp-personas.md": ("gtm_core/content_quality/sources.py",),
    "icp-scoring.toml": ("gtm_core/prospects_backlog.py",),
    # Overlay-aware from the day it was allowlisted (2026-09-22): `scorecard_path()` passes
    # `overlay=` straight to `resolve_knowledge_file`, and the CLI exposes `--overlay`.
    "scorecard.toml": ("gtm_core/scorecard/loader.py",),
    "role-vocabulary.toml": ("gtm_core/role_vocabulary/__init__.py",),
    "hook-matrix.md": (
        "gtm_core/hooks.py",
        "gtm_core/build_eval_sheet.py",
        "gtm_core/content_quality/sources.py",
    ),
    "hooks.toml": ("gtm_core/hooks.py",),
    "premise-vocab.toml": ("gtm_core/hook_coverage/premise.py",),
}

#: Overlayable files with no resolver outside experiments.py. They are admitted by the allowlist
#: and copied into the overlay directory, but nothing in gtm_core reads them by name — a skill
#: reads them through `resolve_knowledge`, which already takes `--overlay`. Listed so the set
#: below stays exhaustive against OVERLAYABLE.
_RESOLVED_ONLY_BY_SKILLS = frozenset({"market-scan-config.md", "case-studies.md"})

#: The measured gap, 2026-09-21: every overlayable file whose readers cannot be told about an
#: overlay. Shrinking this list is the point; it is `strict` so it cannot rot in either
#: direction — wiring a file without removing it here FAILS.
_UNWIRED = frozenset({"hook-matrix.md", "hooks.toml", "premise-vocab.toml"})


def _constants_bound_to(tree: ast.Module, filename: str) -> set[str]:
    """Module-level names bound to ``filename``.

    Without this the extractor is blind to the common idiom of binding the filename to a
    constant and referencing that — which is exactly how ``premise-vocab.toml`` is written, and
    how this contract first reported a gap as wired. A check that cannot see the reference is
    not a check (§R18).
    """
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if node.value.value == filename:
                names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Constant):
            if node.value.value == filename and isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return names


def _functions_naming(path: str, filename: str) -> list[tuple[str, bool]]:
    """``(function name, accepts overlay)`` for every function in ``path`` that names ``filename``,
    whether by string literal or through a module-level constant bound to it."""
    src = (ROOT / path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    aliases = _constants_bound_to(tree, filename)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            segment = ast.get_source_segment(src, node) or ""
            names_it = f'"{filename}"' in segment or f"'{filename}'" in segment
            if not names_it:
                names_it = any(isinstance(n, ast.Name) and n.id in aliases for n in ast.walk(node))
            if names_it:
                params = {a.arg for a in node.args.args + node.args.kwonlyargs}
                out.append((node.name, "overlay" in params))
    return out


def test_the_allowlist_is_fully_accounted_for():
    """Guard the guard: every OVERLAYABLE file must appear in exactly one bucket, so a file
    added to the allowlist cannot slip past this contract by being in none of them."""
    covered = set(_RESOLVERS) | set(_RESOLVED_ONLY_BY_SKILLS)
    missing = set(experiments.OVERLAYABLE) - covered
    assert not missing, (
        f"overlayable file(s) {sorted(missing)} are admitted by the allowlist but appear in no "
        f"bucket here — classify them: name their resolver, or record that only skills read them"
    )
    assert not covered - set(experiments.OVERLAYABLE), "a bucket names a non-overlayable file"


@pytest.mark.parametrize("filename", sorted(set(_RESOLVERS) - _UNWIRED))
def test_a_wired_overlayable_file_has_an_overlay_aware_reader(filename):
    """The targeting half: every module resolving this file accepts an ``overlay`` argument."""
    gaps = []
    for module in _RESOLVERS[filename]:
        for func, accepts in _functions_naming(module, filename):
            if not accepts:
                gaps.append(f"{module}::{func}")
    assert not gaps, (
        f"{filename} is overlayable but these resolvers take no `overlay`: {gaps}. "
        f"An overlay that cannot reach its reader is an experiment that silently runs on the "
        f"live file."
    )


@pytest.mark.parametrize("filename", sorted(_UNWIRED))
@pytest.mark.xfail(
    strict=True,
    reason=(
        "measured gap 2026-09-21: the messaging half of OVERLAYABLE has no overlay-aware "
        "reader, so an ICP experiment varies who you target and not what you say. Wiring it "
        "is a feature decision, not a test fix — see this module's docstring and PENDING.md. "
        "strict=True: wiring a file without removing it from _UNWIRED fails this test."
    ),
)
def test_the_messaging_half_is_still_unwired(filename):
    gaps = []
    for module in _RESOLVERS[filename]:
        for func, accepts in _functions_naming(module, filename):
            if not accepts:
                gaps.append(f"{module}::{func}")
    assert not gaps, f"{filename}: {gaps}"


def test_the_extractor_can_tell_the_two_apart(tmp_path):
    """§R18. The contract rests entirely on `_functions_naming` distinguishing a resolver that
    takes `overlay` from one that does not. If it could not, every test above would pass
    vacuously — including the xfails, which would then fail for the wrong reason."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        '_VOCAB = "hooks.toml"\n\n'
        'def wired(profile, overlay=None):\n    return "hooks.toml"\n\n'
        'def unwired(profile):\n    return "hooks.toml"\n\n'
        "def unwired_via_constant(profile):\n    return _VOCAB\n",
        encoding="utf-8",
    )
    rel = probe.relative_to(ROOT) if probe.is_relative_to(ROOT) else probe
    found = dict(
        _functions_naming(str(rel) if probe.is_relative_to(ROOT) else str(probe), "hooks.toml")
    )
    assert found.get("wired") is True
    assert found.get("unwired") is False
    assert found.get("unwired_via_constant") is False, (
        "the extractor must follow a filename bound to a module constant — missing that is how "
        "an unwired resolver first read as wired here"
    )


def test_resolve_knowledge_file_itself_still_takes_an_overlay():
    """The rung every wired reader depends on. If this regressed, the 'wired' half above would
    keep passing on a signature that no longer resolves an overlay at all."""
    from gtm_core.paths import resolve_knowledge_file

    assert "overlay" in inspect.signature(resolve_knowledge_file).parameters


def test_the_overlay_kill_switch_is_unchanged():
    """This contract must not be read as widening the overlay surface: the feature is still
    governed by its env kill switch, closed by default."""
    assert experiments.ENABLED_ENV == "GTM_EXPERIMENT_OVERLAY_ENABLED"


def test_the_unwired_gap_is_recorded_as_paths_not_just_filenames():
    """The follow-up needs a work list, not a feeling. Print-quality evidence that the gap is
    bounded: every unwired resolver, named, so whoever picks this up knows the size of it."""
    work = []
    for filename in sorted(_UNWIRED):
        for module in _RESOLVERS[filename]:
            for func, accepts in _functions_naming(module, filename):
                if not accepts:
                    work.append(f"{filename} -> {module}::{func}")
    assert work, "the gap list is empty — either it is fixed (remove _UNWIRED) or blind"
    # Bounded, and small enough to be one follow-up rather than a project.
    assert len(work) <= 15, f"the gap grew past a single follow-up: {work}"


def test_the_premise_vocab_reader_bypasses_the_resolver_entirely():
    """A sharper finding than 'takes no overlay', recorded so it is not lost.

    `hook_coverage/premise.py` builds `root / profile / "knowledge" / _PREMISE_VOCAB_FILE` by
    hand instead of calling `paths.resolve_knowledge_file`. So it reaches neither the overlay
    rung NOR the product rung: a tenant with a product-level premise vocabulary is silently
    served the profile-level one today, independently of experiments. Whoever wires overlay
    here should switch it to the resolver rather than add a third rung by hand.
    """
    src = (ROOT / "gtm_core/hook_coverage/premise.py").read_text(encoding="utf-8")
    # The module is INCONSISTENT with itself, which is the sharp part: `capability_vocab`
    # resolves `product.md` through the resolver, while `load_premise_vocab` hand-builds
    # `<root>/<profile>/knowledge/<file>` right beside it. One of the two reaches the product
    # and overlay rungs; the other cannot.
    assert "resolve_knowledge_file" in src, "the resolver import this test contrasts against"
    assert '/ "knowledge" / _PREMISE_VOCAB_FILE' in src, (
        "the hand-built path has moved — if load_premise_vocab now uses the resolver, delete "
        "this test and the PENDING.md note with it"
    )
