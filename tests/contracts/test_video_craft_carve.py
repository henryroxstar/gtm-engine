"""The public cut withholds ``gtm_core/video_craft`` and loses nothing else (test plan 3.B "Carve").

(a) Simulated public cut, in a SUBPROCESS so no import state leaks: a ``sys.meta_path`` finder makes
    ``gtm_core.video_craft`` missing the way the carve leaves it, then the public modules import,
    ``lint_shotlist_detailed`` equals ``lint_shotlist`` on every tripwire shot list with nothing
    "not checked", ``diagram-reveal`` is not registered, and the CLI refuses ``--require-craft``.
(b) Guard audit: every import of ``video_craft`` from a module the cut SHIPS sits inside a ``try``
    whose handler catches ``ImportError`` / ``ModuleNotFoundError``.
(c) ``gtm_core/video_craft`` matches a ``[carve].exclude_mechanics`` pattern, so a rename that
    escapes the ``video_*`` prefix fails here rather than shipping the package.

This file runs in BOTH cuts: nothing here imports ``gtm_core.video_craft``.
"""

from __future__ import annotations

import ast
import fnmatch
import json
import os
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GATING = REPO / "gtm_core" / "gating.toml"
CORPUS = REPO / "tests" / "tripwire" / "shots_lint"


# ── (a) simulated public cut ──────────────────────────────────────────────────────────────────

_PUBLIC_CUT = textwrap.dedent(
    """
    import json, sys
    from pathlib import Path

    class _Absent:
        @staticmethod
        def find_spec(name, path=None, target=None):
            if name == "gtm_core.video_craft" or name.startswith("gtm_core.video_craft."):
                raise ModuleNotFoundError(f"No module named {name!r}", name="gtm_core.video_craft")
            return None

    sys.meta_path.insert(0, _Absent)
    out = {}

    # Instrument check: the finder really makes the package missing.
    try:
        import gtm_core.video_craft  # noqa: F401
        out["blocked"] = False
    except ModuleNotFoundError as exc:
        out["blocked"] = exc.name == "gtm_core.video_craft"

    import gtm_core.shots_lint
    import gtm_core.screen_ui
    import gtm_core.screen_ui.registry as registry
    import gtm_core.preview_card
    import gtm_core.keyframe_request
    from gtm_core.shots_lint import lint_shotlist
    from gtm_core.shots_lint.result import lint_shotlist_detailed, load_craft_hook

    out["hook"] = load_craft_hook() is not None
    out["diagram_reveal_registered"] = "diagram-reveal" in registry._HTML_SCENES
    out["mismatches"] = []
    out["fixtures"] = 0
    for path in sorted(Path(sys.argv[1]).glob("*.shots.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for kw in ({}, {"voice_grade": "professional", "voice_id": "voice-1"}):
            base = lint_shotlist(doc, **kw)
            got = lint_shotlist_detailed(doc, **kw)
            if (got.errors, got.warnings) != tuple(base) or got.not_checked or got.findings:
                out["mismatches"].append(path.name)
        out["fixtures"] += 1

    from gtm_core.shots_lint.cli import main
    first = sorted(Path(sys.argv[1]).glob("*.shots.json"))[0]
    try:
        main([str(first), "--require-craft"])
        out["require_craft_exit"] = 0
    except SystemExit as exc:
        out["require_craft_exit"] = exc.code
    out["leaked"] = sorted(m for m in sys.modules if m.startswith("gtm_core.video_craft"))
    print("RESULT " + json.dumps(out))
    """
)


@pytest.fixture(scope="module")
def public_cut() -> dict:
    if not any(CORPUS.glob("*.shots.json")):
        pytest.skip("the tripwire shot-list corpus is not in this tree")
    env = {k: v for k, v in os.environ.items() if k != "GTM_RUNTIME"}
    proc = subprocess.run(
        [sys.executable, "-c", _PUBLIC_CUT, str(CORPUS)],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert proc.returncode == 0 and lines, f"public-cut probe failed:\n{proc.stderr[-3000:]}"
    return json.loads(lines[-1][len("RESULT ") :])


def test_the_probe_really_removed_the_package(public_cut):
    assert public_cut["blocked"] is True
    assert public_cut["leaked"] == []
    assert public_cut["hook"] is False


def test_the_public_modules_lint_exactly_as_before(public_cut):
    assert public_cut["fixtures"] > 0
    assert public_cut["mismatches"] == []


def test_diagram_reveal_is_not_registered_in_the_public_cut(public_cut):
    assert public_cut["diagram_reveal_registered"] is False


def test_the_cli_refuses_require_craft_in_the_public_cut(public_cut):
    assert public_cut["require_craft_exit"] == 2


# ── (b) guard audit ───────────────────────────────────────────────────────────────────────────


def exclude_patterns() -> tuple[str, ...]:
    data = tomllib.loads(GATING.read_text(encoding="utf-8"))
    return tuple(data["carve"]["exclude_mechanics"])


def excluded(rel: Path, globs: tuple[str, ...]) -> bool:
    """Mirror of ``tests/lint/carve_surface_check.py:_excluded``."""
    rel_str = rel.as_posix()
    return any(fnmatch.fnmatch(part, g) for part in rel.parts for g in globs) or any(
        fnmatch.fnmatch(rel_str, g) or fnmatch.fnmatch(rel_str, f"{g}/*") for g in globs
    )


_CATCHES = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}


def _handler_catches_import_error(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(n, ast.Name) and n.id in _CATCHES for n in names)


def _names_video_craft(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(a.name.split(".")[:2] == ["gtm_core", "video_craft"] for a in node.names)
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if node.level == 0:
            return (
                module == "gtm_core.video_craft"
                or module.startswith("gtm_core.video_craft.")
                or (module == "gtm_core" and any(a.name == "video_craft" for a in node.names))
            )
        if module == "video_craft" or module.startswith("video_craft."):
            return True
        if not module and any(a.name == "video_craft" for a in node.names):
            return True
        return module == "gtm_core" and any(a.name == "video_craft" for a in node.names)
    if isinstance(node, ast.Call):
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name in {"import_module", "__import__"} and node.args:
            arg = node.args[0]
            return isinstance(arg, ast.Constant) and "video_craft" in str(arg.value)
    return False


def unguarded_imports(source: str, label: str = "<src>") -> list[str]:
    """``label:line`` of every video_craft import not inside a try that catches ImportError."""
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    out = []
    for node in ast.walk(tree):
        if not _names_video_craft(node):
            continue
        guarded = False
        child, parent = node, parents.get(node)
        while parent is not None:
            if (
                isinstance(parent, ast.Try)
                and child in parent.body
                and any(_handler_catches_import_error(h) for h in parent.handlers)
            ):
                guarded = True
                break
            child, parent = parent, parents.get(parent)
        if not guarded:
            out.append(f"{label}:{node.lineno}")
    return out


def _shipped_modules() -> list[Path]:
    globs = exclude_patterns()
    return [
        p
        for p in sorted((REPO / "gtm_core").rglob("*.py"))
        if "__pycache__" not in p.parts and not excluded(p.relative_to(REPO), globs)
    ]


def test_every_public_import_of_video_craft_is_guarded():
    shipped = _shipped_modules()
    assert len(shipped) > 50, "instrument check: the walk found almost no shipped modules"
    bad = []
    for path in shipped:
        bad += unguarded_imports(path.read_text(encoding="utf-8"), str(path.relative_to(REPO)))
    assert bad == []


def test_the_audit_sees_the_known_guarded_sites():
    """The instrument check: the audit is looking at the public modules that DO reach video_craft,
    so a clean result above is a finding, not a blind spot."""
    reaching = {
        str(p.relative_to(REPO))
        for p in _shipped_modules()
        if any(_names_video_craft(n) for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))))
    }
    assert {"gtm_core/shots_lint/result.py", "gtm_core/preview_card.py"} <= reaching


@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        ("from gtm_core.video_craft import hook\n", True),
        ("from .video_craft.hook import craft_findings\n", True),
        ("from ..video_craft import scenes\n", True),
        ("from gtm_core import video_craft\n", True),
        ("import gtm_core.video_craft.require\n", True),
        ("import importlib\nimportlib.import_module('gtm_core.video_craft.hook')\n", True),
        ("def f():\n    from .video_craft import craft_verdict\n", True),
        ("try:\n    from .video_craft import hook\nexcept ValueError:\n    pass\n", True),
        (
            "try:\n    pass\nexcept ImportError:\n    from .video_craft import hook\n",
            True,
        ),
        ("try:\n    from .video_craft import hook\nexcept ImportError:\n    hook = None\n", False),
        (
            "try:\n    from ..video_craft.hook import f\n"
            "except ModuleNotFoundError as exc:\n    raise\n",
            False,
        ),
        (
            "def f():\n    try:\n        from .video_craft import cv\n"
            "    except (OSError, ModuleNotFoundError):\n        return None\n",
            False,
        ),
        ("from gtm_core.video_lint import tiers\n", False),
        ("from .video_crafty import x\n", False),
    ],
)
def test_negative_control_the_audit_discriminates(source, flagged):
    assert bool(unguarded_imports(source)) is flagged


# ── (c) withheld by the existing rule ─────────────────────────────────────────────────────────


def test_video_craft_is_withheld_by_an_existing_exclude_pattern():
    globs = exclude_patterns()
    assert excluded(Path("gtm_core/video_craft"), globs)
    assert excluded(Path("gtm_core/video_craft/hook.py"), globs)
    assert excluded(Path("gtm_core/video_craft/html/diagram_reveal/index.html"), globs)


@pytest.mark.parametrize("renamed", ["gtm_core/craft", "gtm_core/film_craft/hook.py"])
def test_a_rename_escaping_the_prefix_would_not_be_withheld(renamed):
    """Negative control: the matcher is not saying yes to everything under gtm_core."""
    assert not excluded(Path(renamed), exclude_patterns())
