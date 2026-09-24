"""The outreach copy gate: one rule engine, one ``RULES_VERSION``, one command line.

Merged 2026-09-24 from ``outreach_pack_linter.py`` (2865 lines) and ``merge_render_linter.py``
(2253). They were already one engine wearing two CLIs — merge_render imported ``lint_email``,
``lint_hedge_stem`` and ``lint_thread_repetition`` from the pack linter and called
``gtm_core.merge_hygiene.check_row`` itself — so "which linter owns this rule" had no answer a
reader could derive, and the two ``RULES_VERSION`` constants disagreed by fifteen days.

**This directory is production code that happens to live under ``tests/``.** It gates every
outbound email before staging, ``.github/workflows/deploy.yml`` treats ``tests/linter/**`` as a
deploy trigger, and ``gtm_core`` imports the seat resolver and the spec parser from here rather
than keeping a second copy. It sits beside its own tests on purpose: a rule and the fixture
that proves it discriminates belong in one place. Entry: ``../outreach_linter.py``.

Layering, lowest first — a module may import only from the ones above it: ``model`` (types,
version, rule inventory), ``text`` (tokenisers + word tables), ``roles`` (the ONE seat
resolver), ``parse`` (parsers, ``{{Tag}}`` renderer, loaders), ``rules_copy`` (one email),
``rules_batch`` (more than one email + the pack entry points), ``rules_render`` (a template
AND a row), ``rules_derivation`` (the spec against the fact registry — the only module that
imports ``gtm_core.messaging``), ``driver`` (``lint_merge_render`` + reports), ``cli``
(argparse + selftests).
"""

from __future__ import annotations

import sys
from pathlib import Path

# This package is imported two ways: as ``tests.linter.outreach`` (pytest, rootdir on the
# path) and as ``outreach`` (the CLI script and ``gtm_core``, which put ``tests/linter`` on
# the path). Either way ``gtm_core`` has to be importable, which it is not when the process
# started inside ``tests/linter``. Same prologue both merged files carried, now written once.
_REPO_ROOT = str(Path(__file__).resolve().parents[3])
if _REPO_ROOT not in sys.path:  # pragma: no cover - import plumbing
    sys.path.insert(0, _REPO_ROOT)

# There is no `__init__.py` under `tests/`, so BOTH spellings resolve under pytest and each
# would build its own module object — two `RULES_VERSION`s, two `_SEAT_RULES`, two vocabulary
# caches. Whichever name imports second wins for its consumers only, so a run's result would
# depend on test ORDER. Aliasing here makes the second import return the first object, so the
# fork is unrepresentable rather than merely discouraged; `_ALIASES` is the pair, stated once,
# that the contract test reads instead of retyping them.
_ALIASES = ("outreach", "tests.linter.outreach")
for _alias in _ALIASES:  # pragma: no cover - import plumbing
    sys.modules.setdefault(_alias, sys.modules[__name__])

from . import (  # noqa: E402
    cli,
    driver,
    model,
    parse,
    roles,
    rules_batch,
    rules_copy,
    rules_derivation,
    rules_render,
    text,
)

# The submodules need the same aliasing, one level down, and it has to happen AFTER the
# `from . import` above: consumers spell them `from outreach.rules_derivation import ...`, and
# a dotted name absent from `sys.modules` is imported through the parent's `__path__` — which
# resolves, to a SECOND copy of the submodule. Aliasing the package alone would move the fork
# rather than close it.
for _alias in _ALIASES:  # pragma: no cover - import plumbing
    for _sub, _obj in list(sys.modules.items()):
        if _sub.startswith(f"{__name__}."):
            sys.modules.setdefault(f"{_alias}.{_sub.rpartition('.')[2]}", _obj)

# Flatten the submodules into the package namespace, so a consumer sees the one flat surface
# the two merged modules exposed — underscore helpers included, since `hook_coverage.config`
# and the suites import `_norm_tokens`, `_anchors` and friends by name. Programmatic rather
# than ~100 lines of explicit re-export: a hand-kept list is a second inventory of the same
# thing, which is the drift `ALL_RULE_IDS` exists to stop.
for _mod in (
    model,
    text,
    roles,
    parse,
    rules_copy,
    rules_batch,
    rules_render,
    rules_derivation,
    driver,
    cli,
):
    globals().update({_k: _v for _k, _v in vars(_mod).items() if not _k.startswith("__")})
del _mod
