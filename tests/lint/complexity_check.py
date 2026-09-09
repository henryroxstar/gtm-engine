#!/usr/bin/env python3
"""Ratchet on production file length — the §R10 gate.

The codebase reached ~87k production lines with no ceiling on file size, and the fat tail
(53 files over 500 lines, three over 3,000) is where reviews get skipped and changes get
risky. A big-bang split would freeze the repo; a ratchet makes the debt NON-INCREASING from
day one and turns every later touch into a chance to shrink it:

  * a file NOT in the allowlist is capped at 500 physical lines (``wc -l`` semantics);
  * a file IN the allowlist may shrink but never grow past its recorded ceiling;
  * a listed file that drops to <= 500 is a STALE entry and fails until it is delisted —
    a generous leftover ceiling is an error, exactly like a stale PII allowlist entry;
  * a ceiling may be RAISED, and a new entry ADDED, only with a dated one-line reason on
    the entry (``path 620  # 2026-09-02 <why>``). Compared against the committed allowlist
    (``--base HEAD`` at pre-commit) so a silent bump is impossible at the moment it is
    made, and greppable forever after. The gate never blocks an urgent fix outright — it
    converts growth into a recorded decision.

Also enforces the retired-patch-path guard (PRD §4.3): after a module is split into a
package, a test that still patches ``old.module.name`` patches the ``__init__`` re-export
binding while the moved code binds the name in its own module — the mock silently stops
intercepting, the real code runs, and CI stays green. ``retired_patch_paths.txt`` lists the
dotted paths splits have retired; no ``patch``/``setattr`` string in ``tests/`` may name one.

Usage:
    python tests/lint/complexity_check.py [--base REF] [paths...]   # check (default: whole scope)
    python tests/lint/complexity_check.py --freeze > tests/lint/complexity_allowlist.txt

Exit 0 clean, 1 on a finding. Stdlib only — it runs in pre-commit before any sync.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST = Path(__file__).resolve().parent / "complexity_allowlist.txt"
RETIRED = Path(__file__).resolve().parent / "retired_patch_paths.txt"
ALLOWLIST_REL = "tests/lint/complexity_allowlist.txt"
RETIRED_REL = "tests/lint/retired_patch_paths.txt"

CAP = 500

# The production surface. `scripts/` holds operator tooling; generated skills ship helper
# scripts under plugin/skills/*/scripts/ (reference snippets under references/ do not ship).
SOURCE_DIRS = ("gtm_core", "agent", "backend", "cockpit", "mcp_server", "scripts")
SKILL_SCRIPTS = "plugin/skills/*/scripts"
# Two of the biggest de-facto production linters live under tests/ and gate real tenant
# content. They are production code by function and must not escape a prod-only scope.
TEST_IMPL_DIRS = ("tests/linter", "tests/lint")
# `profiles/` holds one-off tenant generators (already ruff-excluded); `content/` is state.
EXCLUDE_PARTS = {
    ".venv",
    "__pycache__",
    "node_modules",
    ".git",
    ".pytest_cache",
    "content",
    "profiles",
}

ENTRY_RE = re.compile(r"^(?P<path>\S+)\s+(?P<ceiling>\d+)\s*(?:#\s*(?P<note>.*?))?\s*$")
DATED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\S")
# A mock call that takes a dotted-string target: patch("x.y"), mock.patch('x.y'),
# patch.dict("x.y", …), monkeypatch.setattr("x.y", …). A word match flagged prose.
PATCH_CALL_RE = re.compile(r"\b(?:patch(?:\.(?:dict|multiple))?|setattr)\s*\(")

# ``{path: (ceiling, note)}`` — the parsed allowlist.
Entries = dict[str, tuple[int, str | None]]
# ``(where, what, how to fix)`` — what every check returns and ``main`` prints.
Finding = tuple[str, str, str]


# ---------------------------------------------------------------------------------------
# scope
# ---------------------------------------------------------------------------------------


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def in_scope(path: Path) -> bool:
    """Is this file one the ratchet governs? Tests and fixtures never are."""
    if path.suffix != ".py" or path.name.startswith("test_") or path.name == "conftest.py":
        return False
    rel = _rel(path)
    parts = rel.split("/")
    if EXCLUDE_PARTS & set(parts):
        return False
    if parts[0] in SOURCE_DIRS:
        return True
    if len(parts) >= 4 and parts[0] == "plugin" and parts[1] == "skills" and parts[3] == "scripts":
        return True
    return any(rel.startswith(d + "/") for d in TEST_IMPL_DIRS)


def scope_files() -> list[Path]:
    """Every governed file in the tree (the CI / pytest path; pre-commit passes staged files)."""
    roots = [ROOT / d for d in SOURCE_DIRS + TEST_IMPL_DIRS] + list(ROOT.glob(SKILL_SCRIPTS))
    seen: set[Path] = set()
    for r in roots:
        if not r.is_dir():
            continue
        for f in r.rglob("*.py"):
            if f.is_file() and in_scope(f):
                seen.add(f.resolve())
    return sorted(seen)


def iter_files(paths: list[str]) -> list[Path]:
    """Expand args into governed files. pre-commit passes individual staged files (any file
    type — the allowlist itself included, so its diff is checked); out-of-scope args are
    skipped silently rather than failed, a hook that cries wolf gets skipped."""
    if not paths:
        return scope_files()
    out: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            out.extend(f for f in path.rglob("*.py") if f.is_file() and in_scope(f))
        elif path.is_file() and in_scope(path):
            out.append(path)
    return sorted({f.resolve() for f in out})


def count_lines(path: Path) -> int:
    """Physical lines, ``wc -l`` semantics (newline count) — how every snapshot was derived."""
    return path.read_bytes().count(b"\n")


# ---------------------------------------------------------------------------------------
# allowlist
# ---------------------------------------------------------------------------------------


def parse_allowlist(text: str) -> tuple[Entries, list[str]]:
    """``{path: (ceiling, note)}`` plus a list of malformed lines."""
    entries: Entries = {}
    bad: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = ENTRY_RE.match(line)
        if not m:
            bad.append(f"line {lineno}: {raw!r}")
            continue
        entries[m["path"]] = (int(m["ceiling"]), m["note"])
    return entries, bad


def load_allowlist(path: Path = ALLOWLIST) -> tuple[Entries, list[str]]:
    if not path.exists():
        return {}, []
    return parse_allowlist(path.read_text(encoding="utf-8"))


def ref_resolves(ref: str, repo_root: Path = ROOT) -> bool | None:
    """Does ``ref`` name a commit? ``None`` when git itself is unavailable. A typo in
    ``--base`` must fail the run, never silently disable the raise guard."""
    try:
        proc = subprocess.run(
            # --end-of-options: a "-"-prefixed ref is a revision, never a git option.
            ["git", "rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return proc.returncode == 0


def load_base_allowlist(ref: str, repo_root: Path = ROOT) -> Entries | None:
    """The allowlist as committed at ``ref`` — ``None`` when it cannot be read (no git, or the
    freeze commit itself, where the ref resolves but no allowlist exists there yet)."""
    try:
        proc = subprocess.run(
            ["git", "show", "--end-of-options", f"{ref}:{ALLOWLIST_REL}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    entries, _ = parse_allowlist(proc.stdout)
    return entries


def check_raises(current: Entries, base: Entries) -> list[Finding]:
    """A raised ceiling or a brand-new entry must carry a dated reason written FOR IT — a note
    carried over from an earlier raise records that raise, not this one. Lowering never needs one."""
    findings: list[Finding] = []
    for path, (ceiling, note) in current.items():
        dated = bool(note and DATED_RE.match(note))
        if path not in base:
            if not dated:
                findings.append(
                    (
                        path,
                        f"new allowlist entry (ceiling {ceiling}) without a dated reason",
                        "the list only ever shrinks; a new file is capped at 500 — split it, or for"
                        f" an urgent fix record why:  {path} {ceiling}  # YYYY-MM-DD <why>",
                    )
                )
            continue
        base_ceiling, base_note = base[path]
        if ceiling <= base_ceiling:
            continue
        if not dated:
            what = f"ceiling raised {base_ceiling} -> {ceiling} without a dated reason"
        elif note == base_note:
            what = f"ceiling raised {base_ceiling} -> {ceiling} under the reason recorded for an earlier raise"
        else:
            continue
        findings.append(
            (
                path,
                what,
                f"every raise is its own recorded decision:  {path} {ceiling}  # YYYY-MM-DD <why>",
            )
        )
    return findings


def freeze(files: list[Path] | None = None) -> str:
    """The allowlist text for the current tree: every governed file over the cap, bare."""
    files = scope_files() if files is None else files
    lines = [
        "# tests/lint/complexity_allowlist.txt — the §R10 file-length ratchet (docs/RULES.md).",
        "#",
        "# One entry per production file over 500 lines:  <repo-relative path> <ceiling>",
        "# A listed file may shrink but never grow past its ceiling. When it shrinks, lower the",
        "# ceiling in the same PR; when it drops to 500 or fewer lines, DELETE the entry (a stale",
        "# entry fails the check). Raising a ceiling, or adding an entry, requires a dated reason",
        "# on the line:  path 620  # 2026-09-02 <why this file must grow>  — the check compares",
        "# against the committed list, so growth is a recorded decision, never a silent bump.",
        "# Each raise needs its OWN reason: one written for an earlier raise does not cover a new one.",
        "#",
        "# Regenerate from scratch (drops every note — a re-freeze, not a routine edit):",
        "#   uv run python tests/lint/complexity_check.py --freeze > tests/lint/complexity_allowlist.txt",
        "",
    ]
    for f in files:
        n = count_lines(f)
        if n > CAP:
            lines.append(f"{_rel(f)} {n}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------
# retired patch paths
# ---------------------------------------------------------------------------------------


def load_retired(path: Path = RETIRED) -> list[str]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    uncommented = (raw.split("#", 1)[0].strip() for raw in text.splitlines())
    return [line for line in uncommented if line]


def find_retired_patch_refs(
    retired: list[str], tests_dir: Path = ROOT / "tests"
) -> list[tuple[str, int, str]]:
    """Every ``patch``/``setattr`` line in tests/ whose string target names a retired path.

    A substring check against an explicit list: zero false positives by construction.
    Deliberately NOT a "patch the defining module" rule — correct mock practice patches
    where a name is USED, so a definer-resolution rule would flag correct tests."""
    if not retired:
        return []
    findings: list[tuple[str, int, str]] = []
    quoted = [(p, f'"{p}"', f"'{p}'") for p in retired]
    retired_set = set(retired)
    for f in sorted(tests_dir.rglob("*.py")):
        # Relative parts: an absolute path that happens to contain "profiles" or "content"
        # must not silently exclude the whole tree (the guard's own failure class).
        if EXCLUDE_PARTS & set(f.relative_to(tests_dir).parts):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#") or not PATCH_CALL_RE.search(line):
                continue
            for path, dq, sq in quoted:
                if dq in line or sq in line:
                    findings.append((_rel(f), lineno, path))
        findings.extend((_rel(f), ln, p) for ln, p in _object_patch_refs(text, retired_set))
    return findings


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    """Local name → dotted path from a test file's imports (``import a.b`` binds ``a``;
    attribute chains resolve through :func:`_dotted`)."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    aliases[a.asname] = a.name
                else:
                    root = a.name.split(".")[0]
                    aliases.setdefault(root, root)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for a in node.names:
                aliases[a.asname or a.name] = f"{node.module}.{a.name}"
    return aliases


def _dotted(node: ast.expr, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value, aliases)
        return f"{base}.{node.attr}" if base else None
    return None


def _object_patch_refs(text: str, retired: set[str]) -> list[tuple[int, str]]:
    """``patch.object(mod, "name")`` / ``monkeypatch.setattr(mod, "name", …)`` /
    ``patch.multiple(mod, name=…)`` sites whose resolved target is retired. No quoted dotted
    path, so the line scan is blind to them — yet it is the form the backend suite uses, and
    after a split it goes stale in exactly the same silent way."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    aliases = _import_aliases(tree)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr
        args = node.args
        if attr in ("object", "setattr") and len(args) >= 2 and isinstance(args[1], ast.Constant):
            base = _dotted(args[0], aliases)
            name = args[1].value
            if base and isinstance(name, str) and f"{base}.{name}" in retired:
                hits.append((node.lineno, f"{base}.{name}"))
        elif attr == "multiple" and args:
            base = (
                args[0].value
                if isinstance(args[0], ast.Constant) and isinstance(args[0].value, str)
                else _dotted(args[0], aliases)
            )
            for kw in node.keywords:
                if base and kw.arg and f"{base}.{kw.arg}" in retired:
                    hits.append((node.lineno, f"{base}.{kw.arg}"))
    return hits


# ---------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------


def check_files(files: list[Path], entries: Entries) -> tuple[list[Finding], list[str]]:
    """(findings, notes). Findings fail; notes are the paste-ready lines for shrunk files."""
    findings: list[Finding] = []
    notes: list[str] = []
    for f in files:
        rel = _rel(f)
        n = count_lines(f)
        if rel in entries:
            ceiling = entries[rel][0]
            if n <= CAP:
                findings.append(
                    (
                        rel,
                        f"{n} lines <= {CAP} but still listed (ceiling {ceiling})",
                        f"stale entry — delete its line from {ALLOWLIST_REL}",
                    )
                )
            elif n > ceiling:
                findings.append(
                    (
                        rel,
                        f"{n} lines > ceiling {ceiling}",
                        "a listed file may shrink but never grow: trim it back, or raise the ceiling"
                        f" with a dated reason:  {rel} {n}  # YYYY-MM-DD <why>",
                    )
                )
            elif n < ceiling:
                notes.append(f"{rel} {n}")
        elif n > CAP:
            findings.append(
                (
                    rel,
                    f"{n} lines > {CAP} (unlisted)",
                    "new and unlisted files are capped at 500 — split it; for an urgent fix only,"
                    f" add a dated entry to {ALLOWLIST_REL}:  {rel} {n}  # YYYY-MM-DD <why>",
                )
            )
    return findings, notes


def check_stale_entries(entries: Entries) -> list[Finding]:
    """Entries whose file is gone (renamed/deleted) — the list must describe the tree."""
    return [
        (rel, "listed but the file does not exist", f"delete its line from {ALLOWLIST_REL}")
        for rel in entries
        if not (ROOT / rel).is_file()
    ]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="complexity_check", description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", help="files/dirs to check (default: the whole scope)")
    ap.add_argument("--base", metavar="REF", help="compare the allowlist against REF (raise guard)")
    ap.add_argument("--freeze", action="store_true", help="print a fresh allowlist for this tree")
    args = ap.parse_args(argv)

    if args.freeze:
        sys.stdout.write(freeze())
        return 0

    entries, malformed = load_allowlist(ALLOWLIST)
    findings: list[Finding] = [
        (ALLOWLIST_REL, f"malformed entry: {m}", "format is:  <path> <ceiling>  [# YYYY-MM-DD why]")
        for m in malformed
    ]

    if args.base:
        if ref_resolves(args.base, ROOT) is False:
            findings.append(
                (
                    ALLOWLIST_REL,
                    f"--base {args.base!r} does not resolve to a commit",
                    "the raise guard cannot run against a ref that does not exist — fix the ref"
                    " (pre-commit passes HEAD; CI passes origin/<PR base>)",
                )
            )
        else:
            base = load_base_allowlist(args.base, ROOT)
            if base is None:
                # The freeze commit: the ref resolves but carries no allowlist yet (or git is
                # unavailable, which pre-commit and CI never are). The only legitimate skip.
                print(f"note: {ALLOWLIST_REL} not readable at {args.base}; raise guard skipped")
            else:
                findings += check_raises(entries, base)

    # Every listed file is validated on EVERY run, whatever was passed: an entry edited below
    # its file's size, or left behind by a delete/rename, must fail at pre-commit as well as in
    # CI — the list has to describe the tree at all times.
    listed = {(ROOT / rel).resolve() for rel in entries if (ROOT / rel).is_file()}
    file_findings, notes = check_files(sorted({*iter_files(args.paths), *listed}), entries)
    findings += file_findings + check_stale_entries(entries)

    retired_hits = find_retired_patch_refs(load_retired(RETIRED), ROOT / "tests")
    findings += [
        (
            f"{rel}:{lineno}",
            f"patches retired path {path}",
            "that binding no longer runs the code — re-point the patch at the module where the"
            " moved code USES the name (see docs/RULES.md §R10)",
        )
        for rel, lineno, path in retired_hits
    ]

    if notes:
        print(
            "note: these listed files shrank — lower their ceilings in this PR (paste over the entry):"
        )
        for line in notes:
            print(f"  {line}")

    if not findings:
        return 0

    print(f"\n✗ complexity ratchet (§R10): {len(findings)} finding(s)\n")
    for where, what, fix in findings:
        print(f"  {where}: {what}")
        print(f"      {fix}")
    print(
        f"\n  Rule: docs/RULES.md §R10. Allowlist: {ALLOWLIST_REL}. Retired paths: {RETIRED_REL}."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
