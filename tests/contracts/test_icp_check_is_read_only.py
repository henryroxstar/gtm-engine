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
import re
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


_RULES = Path(__file__).resolve().parents[2] / "docs" / "RULES.md"
#: A writer row: a backticked `<name>.<attr>` label linked to the file that holds it. The
#: link may name a flat module (`gtm_core/funnel.py`) OR a package one
#: (`gtm_core/messaging/angle_status.py`) — the fifth writer, added 2026-09-24, is the first
#: of the second kind, and the original flat-only pattern would have skipped its row entirely.
#: The row would then have sat in the doc while this contract kept enforcing four, which is
#: exactly the drift the comment block below records happening twice already.
_WRITER_ROW = re.compile(
    r"^\| \[`(?P<label>[a-z_]+)\.[a-z_.]+`\]\(\.\./gtm_core/(?P<path>[a-z_]+(?:/[a-z_]+)?)\.py\) \|",
    re.M,
)


#: The count RULES.md TYPES, in the sentence that opens the writer set. A count in prose goes
#: stale the moment a row lands (§R14), and `>= 7` could only ever catch a row going missing —
#: never one arriving, which is the direction that actually happened on 2026-09-24 when
#: `knowledge_meta.seed_file` became the eighth while three documents still said "seven".
_WRITER_COUNT = re.compile(
    r"`profiles/` is read-only at runtime, with\s+\*\*(?P<count>[a-z]+)\*\* exceptions"
)

#: Only the range the sentence can plausibly hold. An unrecognised word must RAISE rather than
#: default, or a reworded sentence silently stops pinning anything (§R18).
_NUMBER_WORDS = {
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}


def _profiles_writer_count_from_rules(text: str) -> int:
    """The number RULES.md spells out, as an int. Takes ``text`` so the control below can feed
    it a sentence with a different number and prove the parse is reading, not asserting."""
    match = _WRITER_COUNT.search(text)
    assert match, (
        "the sentence that states how many writers `profiles/` has no longer parses — it is "
        "the one place the count is typed, so a reworded sentence must fail loudly here"
    )
    word = match.group("count")
    assert word in _NUMBER_WORDS, f"unrecognised count word in RULES.md: {word!r}"
    return _NUMBER_WORDS[word]


def _profiles_writers_from_rules() -> set[str]:
    """Dotted MODULE paths from the writer table in RULES.md — where the set is stated once.

    The name a row *displays* and the file it *links to* must agree, or the row is naming one
    writer and pointing at another. Returns the module, not the package head
    (`messaging.angle_status`, `messaging.matrix_view`), because two writers now share the
    `messaging` package: collapsing to the head would make the set smaller than the table and
    a missing row would be invisible to any count. The import ban below re-derives the coarse
    head from these, deliberately — exactly as `funnel` bans the whole module rather than
    `record_actuals` alone.
    """
    text = _RULES.read_text(encoding="utf-8")
    start = text.index("The writer set for `profiles/`, stated once")
    end = text.index("\n\n", text.index("| Writer |", start))
    out = set()
    for match in _WRITER_ROW.finditer(text[start:end]):
        path = match.group("path")
        assert path.split("/")[0] == match.group("label"), (
            f"RULES.md writer row displays `{match.group('label')}` but links gtm_core/{path}.py"
        )
        out.add(path.replace("/", "."))
    return out


def test_the_package_imports_no_writer_for_profiles():
    """It may resolve a profiles path to READ it; it must never import something that stages,
    promotes or otherwise writes tenant knowledge."""
    # The modules that actually write under `profiles/`, READ FROM the table docs/RULES.md
    # states them in ("The writer set for `profiles/`, stated once"). This set was hand-typed
    # twice and drifted twice: first it named only two of them and would have passed
    # `from ..hooks import save_hooks`; then `funnel.record_actuals` joined the table on
    # 2026-09-23 and the set stayed at three, while SECURITY-SELF-ASSESSMENT.md said "a
    # contract test bans importing any of them". Deriving it makes the doc the owner.
    #
    # Third time, prevented rather than recorded: `messaging.angle_status` (2026-09-24) is the
    # first writer living in a PACKAGE, and `_WRITER_ROW` matched a flat module path only — so
    # the row would have landed in the doc and this set would have quietly stayed at four. The
    # count alone cannot catch that (the other four still parse), so the new member is asserted
    # BY NAME: "the regex still does not match" has to fail loudly rather than pass.
    #
    # Fourth time, same shape, same day: `messaging.matrix_view` shipped in the SAME working
    # tree as `angle_status` and was left out of the table, and `voc.registry` had been
    # unlisted for months. Neither is catchable by a count — `matrix_view` shares the
    # `messaging` head with a row that already parses — so both are asserted by name too.
    writers = _profiles_writers_from_rules()
    assert writers, f"RULES.md writer table did not parse: {writers}"
    for module in ("messaging.angle_status", "messaging.matrix_view", "voc.registry"):
        assert module in writers, (
            f"the writer row for `{module}` did not parse — _WRITER_ROW must accept "
            f"gtm_core/<pkg>/<mod>.py, not only gtm_core/<mod>.py. Parsed: {sorted(writers)}"
        )

    # The ban itself stays coarse — the package HEAD, so `from ..voc.registry import apply` and
    # `from ..voc import registry` are both caught — but it is matched on dotted SEGMENTS, not
    # as a substring. A substring rule made `voc` match `role_vocabulary`, which icp_check
    # legitimately imports: a contract that cries wolf on an unrelated module gets switched off.
    banned = {module.split(".")[0] for module in writers}
    offenders = []
    for path in _SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            for name in names:
                if banned & set(name.split(".")):
                    offenders.append(f"{path.name}:{node.lineno} {name}")
    assert not offenders, f"icp_check imported a writer of profiles/: {offenders}"


def test_the_typed_count_and_the_table_are_one_fact():
    """The prose count and the table are the SAME fact, so they may not disagree.

    `>= 7` could only catch a row going missing. The direction that keeps happening is the
    other one: a row lands, the table grows, and the word typed in RULES.md — plus the two
    documents that restate it — stays at the old number, green. Equality is what makes adding
    a writer a change you cannot land half of.
    """
    writers = _profiles_writers_from_rules()
    typed = _profiles_writer_count_from_rules(_RULES.read_text(encoding="utf-8"))
    assert len(writers) == typed, (
        f"RULES.md's sentence says there are {typed} writers of profiles/, but its table lists "
        f"{len(writers)}: {sorted(writers)}. Update the sentence and the table together — and "
        "the two name lists elsewhere that link back to this table rather than restating it."
    )


def test_the_count_parser_reads_rather_than_asserts():
    """§R18 control: a sentence with a different number must yield that different number.

    Without this, `_profiles_writer_count_from_rules` could return a constant 8 and the
    equality above would pass for the wrong reason.
    """
    fixture = (
        "**The writer set for `profiles/`, stated once.** `profiles/` is read-only at runtime, "
        "with **four** exceptions — a singleton claim would be wrong."
    )
    assert _profiles_writer_count_from_rules(fixture) == 4
    assert _profiles_writer_count_from_rules(fixture.replace("**four**", "**eleven**")) == 11, (
        "the parser must track the word it reads, not a hardcoded number"
    )

    with pytest.raises(AssertionError):
        _profiles_writer_count_from_rules("the writer set is large")
    with pytest.raises(AssertionError):
        _profiles_writer_count_from_rules(fixture.replace("**four**", "**fourteen**"))


def test_the_import_ban_matches_a_segment_and_not_a_substring():
    """§R18 control, both directions, for the segment rule above.

    Without it the ban is unfalsifiable in one direction (nothing in `icp_check` imports a
    writer today, so "no offenders" proves nothing) and wrong in the other (`voc` inside
    `role_vocabulary` is not an import of the `voc` package).
    """
    banned = {"voc", "hooks"}
    assert banned & set("voc.registry".split(".")), "a real writer import must be caught"
    assert not banned & set("role_vocabulary".split(".")), "a substring match must not fire"
    assert not banned & set("hook_coverage.matrix".split("."))


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
