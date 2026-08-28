"""H4: prove tests/conftest.py's shadow-package guard actually fires.

The guard itself runs as a module-level assert at conftest.py IMPORT time (so a real
violation fails the whole suite's collection, loudly, before any test runs) — which
means there is no live test session in which to exercise a genuine violation. This
pins the pure helper function it is built on (``_shadowing_init_files``) instead,
mirroring the "prove the checker actually fires" pattern used for the engine-purity
AST checker in tests/contracts/test_pack_generality.py.

Loaded via ``importlib`` from an explicit path (never a bare ``import conftest``) —
this repo has FOUR files named conftest.py (tests/, tests/agent/, tests/backend/,
tests/cockpit/); a bare import would be exactly the kind of name collision this
module's subject guards against.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load_root_conftest():
    path = REPO / "tests" / "conftest.py"
    spec = importlib.util.spec_from_file_location("tests_root_conftest_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONFTEST = _load_root_conftest()


def test_the_guard_is_currently_a_no_op():
    """Today only tests/journey/__init__.py exists, and "journey" isn't a shadowable
    name — the guard must find nothing to complain about."""
    assert CONFTEST._shadowing_init_files(REPO / "tests") == []


def test_the_checker_detects_a_synthetic_shadow(tmp_path):
    """A tests/backend/__init__.py — a real package name — must be flagged."""
    fake_tests = tmp_path / "tests"
    (fake_tests / "backend").mkdir(parents=True)
    (fake_tests / "backend" / "__init__.py").touch()
    (fake_tests / "journey").mkdir()
    (fake_tests / "journey" / "__init__.py").touch()  # harmless name, must NOT be flagged

    hits = CONFTEST._shadowing_init_files(fake_tests)
    assert [p.parent.name for p in hits] == ["backend"]


def test_the_checker_ignores_non_shadowing_names(tmp_path):
    fake_tests = tmp_path / "tests"
    for name in ("contracts", "lint", "linter", "skills", "smoke", "fixtures"):
        (fake_tests / name).mkdir(parents=True)
        (fake_tests / name / "__init__.py").touch()
    assert CONFTEST._shadowing_init_files(fake_tests) == []


def test_every_shadowable_name_is_individually_detected(tmp_path):
    """Not just "some" detection — every name on the list must be caught on its own,
    so a future edit to the set can't silently drop coverage for one entry."""
    for name in sorted(CONFTEST._SHADOWABLE_PACKAGE_NAMES):
        fake_tests = tmp_path / f"tests-{name}"
        (fake_tests / name).mkdir(parents=True)
        (fake_tests / name / "__init__.py").touch()
        hits = CONFTEST._shadowing_init_files(fake_tests)
        assert [p.parent.name for p in hits] == [name], f"{name!r} was not detected"
