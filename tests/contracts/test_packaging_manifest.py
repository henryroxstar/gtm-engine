"""Packaging-manifest contract: every importable package ships, and nothing phantom is listed.

The three Dockerfiles install the project with a NON-editable ``pip install .`` and
``[tool.setuptools] packages`` is an explicit list. A subpackage that carries an
``__init__.py`` but is missing from that list imports fine from a checkout — and from the
container's working directory, where the source tree happens to sit ahead of site-packages —
and **vanishes from the installed wheel**. Nothing fails until a process runs from another
directory, or the image stops carrying the source tree. On 2026-09-02 fourteen real packages
(all ten ``agent.mcp.*`` connectors, ``gtm_core.{voc,journey,prospects,community_signal}``)
were in exactly that state, and ``backend.schema`` was listed while not being a package at all.

Every module→package split creates a new subpackage; this test is what makes forgetting the
list entry a CI failure instead of a container that cannot import its own code. Stdlib only.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PYPROJECT = REPO / "pyproject.toml"
SOURCE_ROOTS = ("agent", "gtm_core", "backend", "cockpit", "mcp_server")
SKIP_PARTS = {"__pycache__", ".venv", "node_modules"}


def declared_packages() -> set[str]:
    if not PYPROJECT.exists():
        pytest.skip("no pyproject.toml in this tree")
    return set(
        tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["setuptools"]["packages"]
    )


def real_packages() -> set[str]:
    found: set[str] = set()
    for root in SOURCE_ROOTS:
        for init in (REPO / root).rglob("__init__.py"):
            if SKIP_PARTS & set(init.parts):
                continue
            found.add(".".join(init.parent.relative_to(REPO).parts))
    return found


def test_every_importable_package_is_declared():
    missing = sorted(real_packages() - declared_packages())
    assert not missing, (
        "packages with an __init__.py that `pip install .` will NOT ship — add to "
        "[tool.setuptools] packages in pyproject.toml:\n  "
        + "\n  ".join(f'"{m}",' for m in missing)
    )


def test_every_declared_package_exists():
    phantom = sorted(declared_packages() - real_packages())
    assert not phantom, (
        "listed in [tool.setuptools] packages but not a package (no __init__.py) — "
        "remove, or add an __init__.py if it is meant to ship:\n  " + "\n  ".join(phantom)
    )


def test_no_source_package_is_git_ignored():
    """A `.gitignore` pattern that matches a source directory is the same failure one
    layer earlier: `git add` skips the files, the commit ships without them, and the
    installed package is not merely unlisted but absent.

    Found on 2026-09-02: an unanchored runtime-state rule (``runs/``, meant for
    ``content/<profile>/runs/``) also matched the new source package
    ``backend/services/runs/``. Nothing failed locally — the tree on disk was complete.
    """
    import shutil
    import subprocess

    if shutil.which("git") is None or not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    files = sorted(
        str(init.relative_to(REPO))
        for root in SOURCE_ROOTS
        for init in (REPO / root).rglob("*.py")
        if not SKIP_PARTS & set(init.parts)
    )
    # check-ignore echoes back only the paths that ARE ignored; exit 1 means none were.
    proc = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=REPO,
        input="\n".join(files),
        capture_output=True,
        text=True,
        check=False,
    )
    ignored = sorted(p for p in proc.stdout.splitlines() if p)
    assert not ignored, (
        "source files matched by a .gitignore rule — they would not be committed:\n  "
        + "\n  ".join(ignored)
        + "\n\nNarrow the rule in .gitignore, or re-include the directory with a `!` line."
    )


def test_no_committed_test_fixture_is_git_ignored():
    """A fixture a test reads must actually be IN the repo.

    Same failure as the source-package case above, one layer over, and the second time this
    class has landed: `.gitignore`'s `*.mp4` matched `tests/tripwire/.../probe-only.mp4`, so
    the file existed on the machine that wrote it and nowhere else. The content_quality reel
    case then found no asset, skipped its video lint, and exited 0 instead of 1 — green here,
    red on CI, and green for the wrong reason if CI had not run it.

    Fixtures a test GENERATES are fine (they are recreated on demand); this catches the ones
    checked in — or meant to be — under a pattern that quietly excludes them.
    """
    import shutil
    import subprocess

    if shutil.which("git") is None or not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    fixture_roots = [REPO / "tests" / "tripwire", REPO / "tests" / "goldens"]
    files = sorted(
        str(f.relative_to(REPO))
        for root in fixture_roots
        if root.is_dir()
        for f in root.rglob("*")
        if f.is_file() and not SKIP_PARTS & set(f.parts)
    )
    if not files:
        pytest.skip("no fixture trees present in this carve")
    proc = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=REPO,
        input="\n".join(files),
        capture_output=True,
        text=True,
        check=False,
    )
    # A generated placeholder is expected to be ignored AND absent from the index; what this
    # catches is a file that exists, is read by a test, and can never reach another machine.
    ignored = sorted(
        p for p in proc.stdout.splitlines() if p and (REPO / p).exists() and "__pycache__" not in p
    )
    tracked = set(
        subprocess.run(
            ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=False
        ).stdout.splitlines()
    )
    stranded = [p for p in ignored if p not in tracked and not _is_generated(REPO / p)]
    assert stranded == [], (
        "test fixtures matched by a .gitignore rule and not tracked — they exist here and "
        "nowhere else, so a test reading them passes locally and behaves differently on CI:\n  "
        + "\n  ".join(stranded)
        + "\n\nGenerate the file in the fixture, or narrow the .gitignore rule."
    )


def _is_generated(path) -> bool:
    """A placeholder the fixture recreates on demand is not stranded — `support.py` names them."""
    support = REPO / "tests" / "tripwire" / "support.py"
    if not support.is_file():
        return False
    return path.name in support.read_text(encoding="utf-8")
