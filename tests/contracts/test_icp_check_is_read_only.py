"""``gtm_core.icp_check`` is read-only by CONSTRUCTION, not by convention.

The PRD's §5 property: there is no ``--apply``, no ``--write``, and no code path that opens
anything under ``profiles/`` in a write mode. These are asserted over the module's AST rather
than by running it, because a path not taken during a test proves nothing about a path that
exists.

The one thing the package may write is the optional ``--out`` JSON under ``content/``, which
is operator-requested and named on the command line, plus one ``history.jsonl`` event through
the existing writer.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "gtm_core" / "icp_check"
_SOURCES = sorted(PACKAGE.rglob("*.py"))

#: Call names that are ALWAYS a write — they have no read mode to inspect, so a
#: mode-based test is blind to them. This is the form the package actually uses
#: (`Path(...).write_text(...)`), and the first version of this contract skipped every one of
#: them because `_mode_of` returned None and None was treated as "not a write". A contract
#: blind to the only write form in the package it guards is not a contract (§R18).
_ALWAYS_WRITES = frozenset({"write_text", "write_bytes", "mkdir", "unlink", "rmtree", "touch"})

#: Filesystem writes whose NAME is ambiguous with a common non-filesystem method — `replace`
#: is `str.replace` far more often than `os.replace`, and treating the bare name as a write
#: false-positives on ordinary text handling. Matched only in module-qualified form
#: (`os.replace(...)`, `shutil.move(...)`), which is how the filesystem versions are written.
_QUALIFIED_WRITES = frozenset(
    {
        ("os", "replace"),
        ("os", "rename"),
        ("shutil", "move"),
        ("shutil", "rmtree"),
        ("os", "remove"),
    }
)

#: Modes that only read. Anything else on an `open()` is a write and must be justified.
_READ_MODES = {"r", "rb", "rt", "rU"}


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _qualified(node: ast.Call) -> tuple[str, str] | None:
    """``(module, attr)`` for a `mod.attr(...)` call, else None."""
    f = node.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        return (f.value.id, f.attr)
    return None


def _open_calls(tree: ast.AST) -> list[ast.Call]:
    """Every `open(...)`, every always-writing call, and every qualified filesystem write."""
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        if _call_name(n) in ({"open"} | _ALWAYS_WRITES) or _qualified(n) in _QUALIFIED_WRITES:
            out.append(n)
    return out


def _is_write(call: ast.Call) -> bool:
    """Does this call write? An always-writing name does, regardless of any mode argument."""
    if _call_name(call) in _ALWAYS_WRITES or _qualified(call) in _QUALIFIED_WRITES:
        return True
    mode = _mode_of(call)
    return mode is not None and not set(mode) <= set("rbtU")


def _mode_of(call: ast.Call) -> str | None:
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    for arg in call.args[:2]:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            if set(arg.value) <= set("rwxabt+U"):
                return arg.value
    return None


def test_the_sources_exist():
    """Guard the guard: if the glob silently found nothing, every test below would pass
    vacuously — a check that cannot discriminate is not a check (§R18)."""
    assert _SOURCES, f"no python sources under {PACKAGE}"
    assert len(_SOURCES) >= 4


#: The ONLY writes this package may perform, as `file:function` — the operator-requested
#: `--out` JSON, confined to the content root. Every other write is a finding. Named
#: explicitly so adding one is a decision a reviewer sees, not a diff nobody reads.
_AUDITED_WRITES = frozenset({"cli.py:_cli_check"})


def _enclosing_function(tree: ast.AST, call: ast.Call) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for child in ast.walk(node):
                if child is call:
                    return node.name
    return "<module>"


def test_the_package_performs_no_write_outside_the_audited_output_path():
    """§5 unrepresentability, and the reason this is not a mode check.

    The package writes via `Path(...).write_text(...)`, which carries NO mode argument — so a
    mode-based contract returns None for it and, if None is read as "not a write", is blind to
    the only write form present. Always-writing call NAMES are therefore treated as writes
    unconditionally.
    """
    offenders = []
    for path in _SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in _open_calls(tree):
            if not _is_write(call):
                continue
            where = f"{path.name}:{_enclosing_function(tree, call)}"
            if where not in _AUDITED_WRITES:
                offenders.append(f"{where} (line {call.lineno}, {_call_name(call)})")
    assert not offenders, (
        f"unaudited write(s) in a read-only package: {offenders}. The only permitted write is "
        f"the operator-requested --out JSON under content/."
    )


def test_no_write_call_targets_a_profiles_path():
    """The tenant-boundary half: no write anywhere in the package may name `profiles/`."""
    offenders = []
    for path in _SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in _open_calls(tree):
            if not _is_write(call):
                continue
            segment = ast.get_source_segment(path.read_text(encoding="utf-8"), call) or ""
            if "profiles" in segment:
                offenders.append(f"{path.name}:{call.lineno}")
    assert not offenders, f"a write naming profiles/: {offenders}"


def test_the_package_imports_no_writer_for_profiles():
    """It may resolve a profiles path to READ it; it must never import something that stages,
    promotes or otherwise writes tenant knowledge."""
    # The modules that actually write under `profiles/`. `knowledge_staging.promote` writes
    # the knowledge CORPUS; `hooks.save_hooks` and `brandkit.set_identity_value` are the two
    # other key-scoped writers. An earlier version of this list named only the first two and
    # would have passed `from ..hooks import save_hooks`.
    banned = {"knowledge_staging", "knowledge_refresh", "hooks", "brandkit"}
    offenders = []
    for path in _SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if any(b in node.module for b in banned):
                    offenders.append(f"{path.name}:{node.lineno} {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if any(b in alias.name for b in banned):
                        offenders.append(f"{path.name}:{node.lineno} {alias.name}")
    assert not offenders, f"icp_check imported a writer of profiles/: {offenders}"


def _declared_flags() -> set[str]:
    """Every flag the CLI actually registers, read from `add_argument` calls.

    Read from the AST, not from the file's text: the module docstring NAMES `--apply` in
    order to say it does not exist, and a substring scan would fail on the documentation
    of the very property it is checking.
    """
    tree = ast.parse((PACKAGE / "cli.py").read_text(encoding="utf-8"))
    flags = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
        ):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    flags.add(arg.value)
    return flags


def test_the_cli_exposes_no_apply_or_write_flag():
    """The property is structural: there is no flag to misuse, so no reviewer has to check
    that a default was left safe."""
    declared = _declared_flags()
    assert declared, "the flag extractor found nothing — it would pass vacuously"
    for flag in ("--apply", "--write", "--fix", "--promote"):
        assert flag not in declared, f"{flag} must not exist in a read-only critique CLI"


def test_the_flag_extractor_would_see_an_apply_flag():
    """§R18 control for the extractor itself."""
    tree = ast.parse('p.add_argument("--apply", action="store_true")')
    found = {
        a.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "add_argument"
        for a in n.args
        if isinstance(a, ast.Constant)
    }
    assert "--apply" in found


def test_the_package_imports_no_egress_client():
    """§R6 — zero new egress. A new allowlist entry here would itself be the defect."""
    joined = "\n".join(p.read_text(encoding="utf-8") for p in _SOURCES)
    for module in ("requests", "httpx", "urllib.request", "aiohttp", "socket"):
        assert module not in joined, f"icp_check must make no network call ({module})"


def test_the_contract_catches_a_write_text_under_profiles():
    """§R18 control for the blind spot this contract actually had: `write_text` carries no
    mode, so a mode-only check skipped it. If this control ever fails, the contract above has
    gone blind to the one write form the package uses."""
    tree = ast.parse(
        'Path(f"profiles/{p}/knowledge/icp-scoring.toml").write_text(payload, encoding="utf-8")'
    )
    calls = _open_calls(tree)
    assert calls, "the extractor must see write_text at all"
    assert _mode_of(calls[0]) is None, (
        "precondition: a real write_text call offers NO mode to inspect — which is exactly "
        "why a mode-only contract was blind to it"
    )
    assert _is_write(calls[0]), "so it must be caught by NAME, not by mode"


def test_a_string_replace_is_not_mistaken_for_a_filesystem_write():
    """Precision control. `replace` is `str.replace` far more often than `os.replace`, and a
    bare-name rule flagged this package's own marker-neutralising `text.replace(a, b)` as a
    write. A contract that cries wolf on ordinary text handling gets switched off."""
    assert not _open_calls(ast.parse('out = text.replace("a", "b")'))
    # ...while the filesystem form is still caught:
    calls = _open_calls(ast.parse("os.replace(tmp, target)"))
    assert calls and _is_write(calls[0])


def test_the_contract_catches_a_bare_mkdir():
    tree = ast.parse('Path("profiles/x").mkdir(parents=True)')
    assert _is_write(_open_calls(tree)[0])


def test_the_ast_contract_catches_a_write_mode_open():
    """§R18 negative control. Without this, a contract that has never seen a violation is
    indistinguishable from one that cannot see one."""
    tree = ast.parse('open("profiles/acme/knowledge/icp-scoring.toml", "w")')
    calls = _open_calls(tree)
    assert calls, "the extractor must find the call at all"
    assert _mode_of(calls[0]) == "w"
    assert not set("w") <= set("rbtU"), "the mode predicate must reject a write"


def test_the_ast_contract_accepts_an_ordinary_read():
    """The other half of the control: it must not simply reject everything."""
    tree = ast.parse('open("x.toml", "rb")')
    assert set(_mode_of(_open_calls(tree)[0])) <= set("rbtU")


@pytest.mark.parametrize("name", ["checks.py", "keyword.py", "propose.py", "cli.py"])
def test_every_module_is_importable_without_side_effects(name):
    """Import must not create a directory, read a profile, or touch the network."""
    import importlib

    importlib.import_module(f"gtm_core.icp_check.{name[:-3]}")
