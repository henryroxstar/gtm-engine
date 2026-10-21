"""``--overlay`` must reach every reader of an overlayable file — enforced, not enumerated.

An overlay replaces targeting files for the length of ONE run so a different ICP or hook matrix
can be tried without touching the live one. ``experiments.OVERLAYABLE`` admits eight files; the
kill switch and the admission gate are already enforced elsewhere. What was never enforced is
the other half: that the modules which actually RESOLVE those files can be told about the
overlay at all.

Measured 2026-09-21, the answer split cleanly along the two halves of the allowlist:

* **The targeting half was wired.** ``icp-personas.md``, ``icp-scoring.toml`` and
  ``role-vocabulary.toml`` all resolved through functions that accept ``overlay``.
* **The messaging half was not.** ``hook-matrix.md``, ``hooks.toml`` and ``premise-vocab.toml``
  were on the allowlist and no reader of them accepted an ``overlay`` argument.

So an ICP experiment varied **who** you target and not **what you say to them** — you could swap
the rubric and not the hook bank, which made a messaging experiment unrepresentable even though
the allowlist claimed to admit one. The gap shipped as a ``strict`` xfail rather than as a fix,
because threading ``overlay`` through the messaging side is a feature and deserved its own
decision rather than having a test make it.

**Closed 2026-09-23.** ``_UNWIRED`` is empty and the xfails are gone: both halves resolve
through ``overlay=``. What this file enforces now is that it stays that way — a new overlayable
file whose readers cannot be told about an overlay fails immediately rather than joining a gap,
and the three functions that name an overlayable file WITHOUT resolving it are recorded in
``_NOT_RESOLVERS`` with the reason, so an exemption is a decision someone wrote down rather than
a silence.

**A reader accepting the overlay is not the same as the resolver PATH carrying it.** Found
2026-10-09: ``role-vocabulary.toml`` was wired — ``role_vocabulary.load`` took ``overlay`` — and
the one seat resolver (``persona_of`` / ``seat_of`` in ``tests/linter/outreach/roles.py``) called
it without one. ``messaging.resolve.angle_for`` then dropped the overlay it was given before
asking for the seat, so ``--overlay X`` varied the angles, proof and premise vocabulary and never
who a seat was: an experiment that added a persona refused all of its rows as ``seat-unresolved``.
The file-level contract above cannot see that — it checks readers of the FILE, not callers of the
resolver — so the bottom of this module checks the path: behaviourally (an overlay persona seats
under ``overlay=`` and not without), and structurally (every ``seat_of`` / ``persona_of`` call on
the resolver path passes ``overlay=``).
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import sys

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
    "kinetic-chains.toml": ("gtm_core/signal_events/kinetic.py",),
}

#: Overlayable files with no resolver outside experiments.py. They are admitted by the allowlist
#: and copied into the overlay directory, but nothing in gtm_core reads them by name — a skill
#: reads them through `resolve_knowledge`, which already takes `--overlay`. Listed so the set
#: below stays exhaustive against OVERLAYABLE.
_RESOLVED_ONLY_BY_SKILLS = frozenset({"market-scan-config.md", "case-studies.md"})

#: The measured gap, 2026-09-21. **Closed 2026-09-23** — the messaging half now resolves
#: through `overlay=` like the targeting half, so an experiment varies what you say as well
#: as who you say it to. Kept as an empty frozenset rather than deleted: the parametrised
#: xfail that guarded it is gone, and an empty set is what makes
#: `test_a_wired_overlayable_file_has_an_overlay_aware_reader` cover EVERY file below.
_UNWIRED: frozenset[str] = frozenset()

#: ``(module, function) -> why it names an overlayable file and is still not a resolver.``
#:
#: `_functions_naming` reads the source for the filename; it cannot tell a resolver from a
#: function that merely mentions one. Three real cases, each exempt for a different reason
#: and each recorded here rather than silently skipped — an unexplained exemption is how a
#: contract quietly stops covering the thing it was written for.
_NOT_RESOLVERS: dict[tuple[str, str], str] = {
    (
        "gtm_core/hooks.py",
        "_parse_hook_matrix",
    ): (
        "a PARSER, handed an already-resolved path. It names the filename only as the "
        "`authoritative` sentinel value on the bank it returns ('hooks.toml' | "
        "'hook-matrix.md'), which is a fact about which file won, not a path it built. "
        "Giving it an `overlay` it could not use would be an unused parameter pretending "
        "to be a capability."
    ),
    (
        "gtm_core/content_quality/sources.py",
        "_person_hook_matrix_path",
    ): (
        "a DIFFERENT file that shares the name. It reads a personal matrix from a fixed "
        "path outside `profiles/` entirely, so there is no tenant, no experiments "
        "directory, and nothing an overlay slug could select. The filename collision is "
        "the only thing this has in common with the overlayable file."
    ),
    (
        "gtm_core/hooks.py",
        "promote_hooks_toml",
    ): (
        "a WRITER — it calls `save_hooks`, one of the three key-scoped writers into "
        "`profiles/`. An overlay is a read-time experiment rung; a promote that could "
        "target `profiles/<t>/experiments/<slug>/` would make an experiment authoritative, "
        "which is a capability no PRD has authorised. Its absence here is the point."
    ),
}


#: ``function -> the caller gap``. A reader that ACCEPTS ``overlay`` and that no caller can
#: pass one to is wired and unreachable — the dead-code shape the backend Gate-2
#: ``_dispatch_gate2`` incident produced, where a fabricated state made every test pass
#: against code nothing invoked.
#:
#: This contract's extractor asks only whether a parameter EXISTS, so it cannot tell the two
#: apart on its own; ``test_messaging_overlay_reaches_the_copy.py`` calls each function
#: directly with ``overlay=`` and cannot either. Found by review 2026-09-23, after
#: ``_UNWIRED`` was emptied. Recorded here so "the messaging half is wired" is not read as
#: "a messaging overlay reaches every messaging file" — it does not yet reach these three.
#:
#: Tracked in PENDING.md. Shrinking this to empty is what finishes P1.
_NO_CALLER_REACH: dict[str, str] = {
    "load_hooks": (
        "gtm_core/hooks.py — no caller passes `overlay=`. Its CLI offers `--product` and no "
        "`--overlay`, and its in-repo consumers (hooks_lint, gtm_distill, hook_score, "
        "content_quality/script.py and pre.py, cockpit/hooks.py) all omit it. So an overlay "
        "carrying a `hooks.toml` still changes nothing — which is the exact condition the "
        "PENDING item tracked before the parameter existed."
    ),
    "load_hook_matrix": (
        "gtm_core/content_quality/sources.py — its sole caller, content_quality/post.py, "
        "passes `(profiles_root, profile)` only."
    ),
    "load_premise_vocab": (
        "gtm_core/hook_coverage/premise.py — its sole production caller, "
        "gtm_core/rule_baseline.py, passes `(profile)` only. The `hook_coverage` CLI's own "
        "`--overlay` does not reach it either: nothing in that package calls this function."
    ),
}


def test_the_caller_gap_is_recorded_rather_than_implied_closed():
    """`_UNWIRED` being empty means every reader ACCEPTS an overlay. It does not mean every
    reader can be GIVEN one, and conflating the two is how a wired-but-unreachable parameter
    reads as a shipped feature.

    This asserts the gap list is honest in both directions: every name in it is a real
    function that really takes `overlay`, and the reasons are specific enough to act on. It
    deliberately does not assert the gap is empty — that is the follow-up, tracked in
    PENDING.md, and a test that failed until someone did it would be a test nobody could run.
    """
    accepts = {
        func
        for filename, modules in _RESOLVERS.items()
        for module in modules
        for func, ok in _functions_naming(module, filename)
        if ok
    }
    stale = sorted(set(_NO_CALLER_REACH) - accepts)
    assert not stale, (
        f"{stale} are recorded as unreachable but no longer take an `overlay` at all — "
        f"either they were reverted or renamed, and the note now describes nothing"
    )
    for func, reason in _NO_CALLER_REACH.items():
        assert "gtm_core/" in reason and len(reason) > 60, (
            f"{func}'s note must name where the gap is, or the follow-up starts from scratch"
        )


def test_the_two_wired_clis_do_reach_their_readers():
    """The positive half, and what keeps the note above from being an excuse.

    Two CLIs pass `overlay=` through to a resolver, so the messaging overlay is reachable by
    an operator today for the matrix — just not yet for the hook bank or the premise
    vocabulary. Asserted on the source of the CLI modules, because a flag that parses and is
    then dropped on the floor is the same defect one layer up.
    """
    for module, needle in (
        ("gtm_core/hook_coverage/cli.py", "overlay=args.overlay"),
        ("gtm_core/build_eval_sheet.py", "overlay=args.overlay"),
    ):
        src = (ROOT / module).read_text(encoding="utf-8")
        assert '"--overlay"' in src, f"{module} does not offer --overlay"
        assert needle in src, f"{module} parses --overlay and never passes it on"


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
    """Every module resolving this file accepts an ``overlay`` argument.

    Both halves of the allowlist, since 2026-09-23. An overlay that cannot reach its reader
    is an experiment that silently runs on the live file — and the reader it could not reach
    would be the one deciding what the copy says.
    """
    gaps = []
    for module in _RESOLVERS[filename]:
        for func, accepts in _functions_naming(module, filename):
            if accepts or (module, func) in _NOT_RESOLVERS:
                continue
            gaps.append(f"{module}::{func}")
    assert not gaps, (
        f"{filename} is overlayable but these resolvers take no `overlay`: {gaps}. "
        f"An overlay that cannot reach its reader is an experiment that silently runs on the "
        f"live file. If one of these is genuinely not a resolver, add it to _NOT_RESOLVERS "
        f"WITH ITS REASON rather than leaving it out of the list."
    )


def test_every_exemption_still_names_something_real():
    """Guard the exemptions: a stale one silently stops covering a live resolver.

    Each must still be a function that exists, still names the file, and still takes no
    `overlay` — the moment any of those stops being true the entry is describing code that
    is no longer there, and the next resolver added under that name inherits a free pass.
    """
    named = {
        (module, func)
        for filename, modules in _RESOLVERS.items()
        for module in modules
        for func, accepts in _functions_naming(module, filename)
        if not accepts
    }
    stale = sorted(set(_NOT_RESOLVERS) - named)
    assert not stale, (
        f"{stale} are exempted from the overlay contract but no longer match a function "
        f"that names an overlayable file and takes no `overlay` — delete the exemption"
    )
    for key, reason in _NOT_RESOLVERS.items():
        assert len(reason) > 40, f"{key} is exempted without a usable reason"


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


def test_the_gap_is_closed_and_the_extractor_can_still_see_a_gap():
    """§R18. `_UNWIRED` being empty must mean "nothing is unwired", never "the extractor
    stopped looking". So: assert the set is empty AND that the extractor still finds the
    three exempted namers, which are the only functions left that name an overlayable file
    and take no `overlay`. If the extractor went blind, that second assertion fails too.
    """
    assert _UNWIRED == frozenset()
    found = {
        (module, func)
        for filename, modules in _RESOLVERS.items()
        for module in modules
        for func, accepts in _functions_naming(module, filename)
        if not accepts
    }
    assert found == set(_NOT_RESOLVERS), (
        f"the only functions naming an overlayable file without an `overlay` argument must "
        f"be the recorded exemptions. Found {sorted(found)}."
    )


def _functions_hand_building_a_knowledge_path(path: str, filename: str) -> list[str]:
    """Functions in ``path`` that join ``knowledge`` onto ``filename`` with ``/`` themselves.

    Matched on the join, not on the two names appearing anywhere in the same function: a
    function may legitimately resolve an overlayable file through the resolver *and* read some
    unrelated nested file under ``knowledge/`` (``load_profile_facts`` does exactly that with
    the platform playbooks). Only ``… / "knowledge" / <the overlayable file>`` is the defect.
    """
    src = (ROOT / path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    aliases = _constants_bound_to(tree, filename)

    def _is_the_file(node: ast.AST) -> bool:
        if isinstance(node, ast.Constant) and node.value == filename:
            return True
        return isinstance(node, ast.Name) and node.id in aliases

    out = []
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(func):
            if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
                continue
            if not _is_the_file(node.right):
                continue
            if any(
                isinstance(n, ast.Constant) and n.value == "knowledge" for n in ast.walk(node.left)
            ):
                out.append(func.name)
                break
    return out


@pytest.mark.parametrize("filename", sorted(_RESOLVERS))
def test_a_reader_of_an_overlayable_file_never_hand_builds_its_path(filename):
    """No resolver of an overlayable file may spell ``knowledge/`` itself.

    Replaces the 2026-09-21 finding that `load_premise_vocab` built
    ``root / profile / "knowledge" / <file>`` by hand while `capability_vocab` two functions
    below it used `resolve_knowledge_file` — so one reached the product and overlay rungs and
    the other could reach neither. Fixed 2026-09-23, and kept as a forward guard rather than
    deleted with the bug: the failure was not "premise.py is wrong", it was that a second way
    to spell a knowledge path existed beside the resolver at all. A reader that hand-builds
    the path silently serves the profile-level file to a tenant who configured a product-level
    one, and cannot be wired for an overlay later without first being rewritten.

    Scoped to functions that name an overlayable file: unrelated nested reads (a platform
    playbook under ``knowledge/playbooks/``) are not resolver-eligible and are not the defect.
    """
    offenders = [
        f"{module}::{func}"
        for module in _RESOLVERS[filename]
        for func in _functions_hand_building_a_knowledge_path(module, filename)
    ]
    assert not offenders, (
        f"{offenders} build a `<root>/<profile>/knowledge/{filename}` path by hand instead of "
        f"calling paths.resolve_knowledge_file. That skips the product rung and the overlay "
        f"rung, so the file read is not the file the tenant configured."
    )


# --- the resolver path ----------------------------------------------------------------------
#
# Everything above asks whether a reader of an overlayable FILE can be told about an overlay.
# These ask whether the one place that turns a title into a seat is told — and whether the
# callers that already hold an overlay hand it on. The two are different contracts: the first
# held for `role-vocabulary.toml` from 2026-09-21 while the second was false until 2026-10-09.

#: Modules on the seat-resolution path that already HOLD an overlay when they resolve a seat.
#: Every `seat_of(...)` / `persona_of(...)` call in each must pass `overlay=`; a call that omits
#: it is the 2026-10-09 defect re-opened one layer up — a wired reader nobody reaches.
_RESOLVER_PATH: tuple[str, ...] = (
    "gtm_core/messaging/resolve.py",
    "gtm_core/hook_coverage/audit.py",
    "gtm_core/hook_coverage/matrix.py",
    "gtm_core/build_eval_sheet.py",
    "gtm_core/cells.py",
)

_SEAT_RESOLVERS = frozenset({"seat_of", "persona_of"})

_BASE_VOCABULARY = (
    'default_persona = "owner"\n'
    'segments = ["startup", "unspecified"]\n'
    "[[persona]]\nname = 'owner'\ncues = ['owner']\n"
    "[[seat]]\nname = 'exec'\npersonas = ['owner']\nstakes = ['margin']\n"
)

#: The base vocabulary plus ONE persona the live file lacks, mapped onto a seat of its own.
_OVERLAY_VOCABULARY = _BASE_VOCABULARY + (
    "[[persona]]\nname = 'treasury'\ncues = ['treasury']\n"
    "[[seat]]\nname = 'finance'\npersonas = ['treasury']\nstakes = ['liquidity']\n"
)


def _seat_resolver_calls(path: str) -> list[tuple[str, int, bool]]:
    """``(callee, line, passes overlay)`` for every ``seat_of`` / ``persona_of`` call in ``path``.

    Matches the bare name and the attribute form (``config.seat_of(...)``) so an import style
    change cannot hide a call from this contract."""
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name in _SEAT_RESOLVERS:
            out.append((name, node.lineno, any(k.arg == "overlay" for k in node.keywords)))
    return out


@pytest.mark.parametrize("module", _RESOLVER_PATH)
def test_every_seat_resolution_on_the_overlay_path_passes_the_overlay(module):
    """A module that holds an overlay and resolves a seat without it has silently fallen back
    to the live vocabulary — the exact shape `angle_for` had until 2026-10-09."""
    calls = _seat_resolver_calls(module)
    assert calls, (
        f"{module} no longer calls seat_of/persona_of at all — either the resolution moved "
        f"(follow it and re-point this list) or the extractor went blind (§R18)"
    )
    dropped = [f"{module}:{line} {name}()" for name, line, ok in calls if not ok]
    assert not dropped, (
        f"{dropped} resolve a seat without `overlay=` while the surrounding module already "
        f"holds one. The overlay reaches the vocabulary file; it has to reach the call too."
    )


def test_the_call_extractor_can_tell_a_threaded_call_from_a_dropped_one(tmp_path):
    """§R18 for the test above: if the extractor could not see the keyword, every module would
    pass by finding nothing wrong rather than by being right."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "def f(title, profile, overlay):\n"
        "    a = seat_of(title, profile, overlay=overlay)\n"
        "    b = persona_of(title, profile)\n"
        "    c = config.seat_of(title, profile)\n",
        encoding="utf-8",
    )
    tree = ast.parse(probe.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in _SEAT_RESOLVERS:
                found.append((name, any(k.arg == "overlay" for k in node.keywords)))
    assert found == [("seat_of", True), ("persona_of", False), ("seat_of", False)]


def _seat_resolver():
    """The ONE resolver, imported the way `gtm_core.hook_coverage.config` imports it."""
    linter = ROOT / "tests" / "linter"
    if str(linter) not in sys.path:
        sys.path.insert(0, str(linter))
    from outreach import non_buyer_of, persona_of, seat_of  # noqa: PLC0415

    return persona_of, seat_of, non_buyer_of


def test_an_overlay_persona_seats_under_the_overlay_and_not_without_it(tmp_path, monkeypatch):
    """The behavioural half, on a throwaway profile.

    The overlay's ``role-vocabulary.toml`` adds a persona cue the live file lacks. The same
    title must resolve to that persona's seat when the overlay is passed, and to nothing when
    it is not — the live vocabulary is never widened by an experiment existing beside it. The
    kill switch is set the way an admitted run would have it, and the profile is a tmp tree:
    nothing here reads or writes a real tenant.
    """
    from gtm_core import role_vocabulary

    profile, slug = "tenant", "treasury-seat-2026-10"
    knowledge = tmp_path / profile / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "role-vocabulary.toml").write_text(_BASE_VOCABULARY, encoding="utf-8")
    experiment = tmp_path / profile / "experiments" / slug
    experiment.mkdir(parents=True)
    (experiment / "role-vocabulary.toml").write_text(_OVERLAY_VOCABULARY, encoding="utf-8")

    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path))
    monkeypatch.setenv(experiments.ENABLED_ENV, "1")
    persona_of, seat_of, _ = _seat_resolver()
    role_vocabulary.clear_cache()
    try:
        title = "Director of Treasury Operations"
        # Live vocabulary: the persona does not exist, so the title seats nowhere.
        assert persona_of(title, profile) is None
        assert seat_of(title, profile) is None
        # Under the overlay: the experiment's persona, and the seat it maps onto.
        assert persona_of(title, profile, overlay=slug) == "treasury"
        assert seat_of(title, profile, overlay=slug) == "finance"
        # A title both files seat resolves the same either way — the overlay ADDS, it
        # does not re-seat what the tenant already defined.
        assert seat_of("Owner", profile) == seat_of("Owner", profile, overlay=slug) == "exec"
        # An overlay that ships no vocabulary of its own falls through to the live file.
        assert seat_of(title, profile, overlay="no-such-experiment") is None
    finally:
        role_vocabulary.clear_cache()


def test_the_resolver_signatures_take_an_overlay():
    """The three public resolver functions, not only `seat_of`: `non_buyer_of` reads the same
    file for the same run and would otherwise judge an experiment's recipients by the live
    tenant's non-buyer list."""
    for fn in _seat_resolver():
        params = inspect.signature(fn).parameters
        assert "overlay" in params, f"{fn.__name__} takes no `overlay`"
        assert params["overlay"].default is None, f"{fn.__name__}: overlay must default to None"
