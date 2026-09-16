"""Guards for ``tests/lint/affected_tests.py`` — the DoD "tests that import what you changed" gate.

The selector exists because the grep it replaced silently missed ``from pkg.sub import mod``. So
the properties pinned here are: the old grep really does miss that form (the negative control —
without it these tests could pass against a selector no better than the grep), every import form
the tree actually uses is caught, an unrelated test is NOT selected (a selector that returns
everything discriminates nothing), and on the real tree it never selects less than the old grep.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
from pathlib import Path

import affected_tests as at
import pytest

ROOT = Path(__file__).resolve().parents[2]
GREP = shutil.which("grep")


def old_grep(changed_rel: str, root: Path) -> set[str]:
    """The replaced recipe, verbatim: ``grep -rlF "<dotted>" tests --include='test_*.py'``."""
    proc = subprocess.run(
        [GREP, "-rlF", at.dotted_path(changed_rel), "tests", "--include=test_*.py"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return set(proc.stdout.split())


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(body, encoding="utf-8")
    return tmp_path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _tree(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/sub/__init__.py": "",
            "pkg/sub/worker.py": "def run(): ...\n",
            "pkg/sub/other.py": "",
            "tools/loose.py": "",
            "tests/unit/test_from_module.py": "from pkg.sub import worker as w\n",
            "tests/unit/test_dotted.py": "import pkg.sub.worker\n",
            "tests/unit/test_symbol.py": "from pkg.sub.worker import run\n",
            "tests/unit/test_lazy.py": "def f():\n    from pkg.sub import worker\n",
            "tests/unit/test_patch.py": 'TARGET = "pkg.sub.worker.run"\n',
            "tests/unit/test_path.py": 'P = "tools/loose.py"\n',
            "tests/unit/test_unrelated.py": "from pkg.sub import other\n",
            "tests/unit/_helpers.py": "from pkg.sub import worker\n",
            "tests/unit/_wrapper.py": "from tests.unit import _helpers\n",
            "tests/unit/test_via_helper.py": "from tests.unit._wrapper import x\n",
            "tests/side/sibling.py": "",
            "tests/side/test_sibling.py": "import sibling\n",
            "tests/pkgtests/__init__.py": "",
            "tests/pkgtests/helpers.py": "",
            "tests/pkgtests/test_relative.py": "from . import helpers\n",
        },
    )


# --- the negative control ------------------------------------------------------------- #


@pytest.mark.skipif(GREP is None, reason="grep not on PATH")
def test_old_grep_misses_from_package_import_module_and_the_selector_catches_it(repo: Path) -> None:
    old = old_grep("pkg/sub/worker.py", repo)
    assert "tests/unit/test_from_module.py" not in old
    assert "tests/unit/test_dotted.py" in old  # the grep is not simply broken
    assert "tests/unit/test_from_module.py" in at.select(["pkg/sub/worker.py"], repo)


# --- import forms --------------------------------------------------------------------- #


def test_every_import_form_selects_and_an_unrelated_sibling_does_not(repo: Path) -> None:
    got = set(at.select(["pkg/sub/worker.py"], repo))
    assert got == {
        "tests/unit/test_from_module.py",
        "tests/unit/test_dotted.py",
        "tests/unit/test_symbol.py",
        "tests/unit/test_lazy.py",
        "tests/unit/test_patch.py",
        "tests/unit/test_via_helper.py",  # two helper hops: the fixpoint
    }


def test_a_changed_package_init_selects_every_importer_of_the_package(repo: Path) -> None:
    got = set(at.select(["pkg/sub/__init__.py"], repo))
    assert {"tests/unit/test_from_module.py", "tests/unit/test_unrelated.py"} <= got
    assert "tests/side/test_sibling.py" not in got


def test_repo_relative_path_reference_selects(repo: Path) -> None:
    assert at.select(["tools/loose.py"], repo) == ["tests/unit/test_path.py"]


def test_bare_import_resolves_through_the_pytest_basedir(repo: Path) -> None:
    assert at.module_names("tests/side/sibling.py", repo) == {"tests.side.sibling", "sibling"}
    assert at.select(["tests/side/sibling.py"], repo) == ["tests/side/test_sibling.py"]


def test_relative_import_resolves_against_the_test_package(repo: Path) -> None:
    assert at.select(["tests/pkgtests/helpers.py"], repo) == ["tests/pkgtests/test_relative.py"]


def test_changed_test_selects_itself_unless_deleted_and_non_py_is_ignored(repo: Path) -> None:
    got = at.select(["tests/unit/test_unrelated.py", "tests/unit/test_gone.py", "README.md"], repo)
    assert got == ["tests/unit/test_unrelated.py"]


def test_unparseable_test_is_selected_loudly(repo: Path, capsys: pytest.CaptureFixture) -> None:
    _tree(repo, {"tests/unit/test_broken.py": "def (:\n"})
    assert "tests/unit/test_broken.py" in at.select(["pkg/sub/other.py"], repo)
    assert "unparseable tests/unit/test_broken.py" in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_a_bad_base_ref_fails_instead_of_reading_as_nothing_changed(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    with pytest.raises(SystemExit, match="failed"):
        at.changed_paths("no-such-ref", repo)


# --- real-data properties ------------------------------------------------------------- #


def test_real_tree_this_selector_is_caught_where_the_old_grep_is_not() -> None:
    """This file imports its subject bare, through the tests/lint sys.path insert — a form the
    dotted grep misses. (Do not spell that dotted path in this file: the grep would find it.)"""
    rel = "tests/lint/affected_tests.py"
    assert "tests/lint/test_affected_tests.py" in at.select([rel])
    if GREP:
        assert "tests/lint/test_affected_tests.py" not in old_grep(rel, ROOT)


def _real_from_package_importers(package: str, module: str) -> list[str]:
    hits = []
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(n, ast.ImportFrom)
            and n.module == package
            and any(a.name == module for a in n.names)
            for n in ast.walk(tree)
        ):
            hits.append(path.relative_to(ROOT).as_posix())
    return hits


@pytest.mark.skipif(GREP is None, reason="grep not on PATH")
def test_real_tree_from_backend_services_runs_import_fake() -> None:
    importers = _real_from_package_importers("backend.services.runs", "fake")
    if not importers:
        pytest.skip("no test uses `from backend.services.runs import fake` any more")
    changed = "backend/services/runs/fake.py"
    missed = set(importers) - old_grep(changed, ROOT)
    assert missed, "the old grep now catches every importer — this control no longer discriminates"
    assert missed <= set(at.select([changed]))


@pytest.mark.skipif(GREP is None, reason="grep not on PATH")
def test_real_tree_never_selects_less_than_the_old_grep() -> None:
    changed = sorted(
        p.relative_to(ROOT).as_posix() for p in (ROOT / "backend/services/runs").glob("*.py")
    )
    assert changed, "the sample package moved — point this at another real package"
    old = set().union(*(old_grep(rel, ROOT) for rel in changed))
    new = set(at.select(changed))
    assert old <= new
    assert len(new) > len(old)
