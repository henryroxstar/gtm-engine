"""Every command a shipped skill cites must parse.

The skill is an executable procedure whose gates are deterministic CLIs. A cited command that
does not parse fails *individually and silently*, and the run degrades into prose that looks like
a finished result (the 2026-09-07 incident the skill's own Step 0 describes). Nothing tied the
cited command lines to the real argparse surfaces, so on 2026-09-21 two of the newest steps in the
``prospect`` skill were found citing commands that exit 2 on the first invocation (PSK-001,
PSK-002). That audit covered one skill; this gate covers every shipped skill, because nothing
about the failure was specific to prospecting — a cited command is prose until something runs it.

Sibling gate: :mod:`tests.lint.test_skill_command_corpus` asks whether a cited command is
*permitted* at runtime. This one asks whether it *parses*. A command can pass either and fail the
other, and both failures look identical from a run: silence, then a plausible-looking result
assembled by hand.

Two checks per citation:

* every ``--flag`` the skill writes exists on the real parser (all citations);
* every flag the parser REQUIRES is written in the citation (fenced ``bash`` blocks only — an
  inline mention is often deliberately partial, a fenced block is what gets pasted and run).

The second check exists because the first one alone reported a clean bill of health for a
command missing a required argument: a check that cannot discriminate is not a check (§R18).
"""

from __future__ import annotations

import re
import subprocess
import sys
from functools import cache
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILLS = REPO / "plugin" / "skills"
#: Every doc a skill ships. `references/**/*.md` rather than `references/*.md`: the
#: provider adapters live one level deeper (`references/providers/saleshandy.md`) and
#: were therefore checked by NOTHING — a command quoted in an adapter could name a CLI
#: that does not exist, which is exactly the "declared contract nobody runs" shape
#: (§R18). Widened 2026-09-21 before SC2 added any command to those files.
DOCS = [*sorted(SKILLS.glob("*/SKILL.md")), *sorted(SKILLS.glob("*/references/**/*.md"))]

#: A `|` ends the command (a shell pipe, a markdown table cell) — except in two shapes where
#: stopping would hide every flag cited after it: inside a `<a|b>` placeholder, and between two
#: word characters, which is how a skill spells an enum VALUE (`--kind character|environment`).
#: Both a shell pipe and a table separator are written with spaces around them; an enum is not.
_CMD = re.compile(
    r"python3?\s+-m\s+(gtm_core(?:\.[A-Za-z_]\w*)+)((?:<[^>\n`]*>|(?<=\w)\|(?=\w)|[^\n`|])*)"
)
_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")
_SUB = re.compile(r"[a-z][a-z0-9-]*")

#: Citations known to be broken, keyed by (module, subcommand, problem). Each is a strict xfail:
#: the suite stays green for everyone sharing the tree, and flips loudly the moment the defect is
#: fixed so the entry gets deleted rather than rotting.
#: Empty, and meant to stay that way. Pointed at every skill for the first time on 2026-09-21 this
#: found three commands that exit 2 as written — `prospects consolidate` missing the dispatcher's
#: nested verb, `prospects integrity` missing the `--profile` its own surrounding prose tells the
#: reader to pass, and video-footage's cap precheck missing `--tool`/`--unit`. All three were fixed
#: in the skills' `body_template.md` the same day and regenerated, so the dict is empty again.
KNOWN_DEFECTS: dict[tuple[str, tuple[str, ...], str], str] = {}


def _trim(line: str, m: re.Match[str]) -> str:
    """The argument text that belongs to THIS command and no other.

    Two nesting directions, both seen in shipped skills, both of which report a correct command
    as broken. Outward: `verify --ledger "$(... paths --profile P)"` — the inner command's flags
    are not the outer parser's. Inward: a line that OPENS a substitution before the match, and
    carries more of the OUTER command after it — the inner command ends at its `)`.
    """
    rest = m.group(2).split(" || ")[0]
    before = line[: m.start()]
    if before.count("$(") > before.count(")"):  # we are inside a substitution: it ends at `)`
        return rest.split(")")[0]
    return _drop_substitutions(rest)


def _drop_substitutions(rest: str) -> str:
    """Remove each balanced ``$(...)`` span, KEEPING what follows it.

    Truncating at the first ``$(`` instead drops the outer command's later flags — which reported
    `suppression verify --ledger "$(...)" --target C` as missing the `--target` it plainly cites.
    """
    out, i, depth = [], 0, 0
    while i < len(rest):
        if rest.startswith("$(", i):
            depth += 1
            i += 2
        elif depth and rest[i] == ")":
            depth -= 1
            i += 1
        else:
            if not depth:
                out.append(rest[i])
            i += 1
    return "".join(out)


def _citations() -> list[tuple[str, tuple[str, ...], tuple[str, ...], bool, str]]:
    found = []
    for doc in DOCS:
        in_fence = False
        buf = ""
        for raw in doc.read_text(encoding="utf-8").splitlines():
            if raw.lstrip().startswith("```"):
                in_fence = not in_fence
                buf = ""
                continue
            line = buf + raw
            if line.rstrip().endswith("\\"):
                buf = line.rstrip()[:-1] + " "
                continue
            buf = ""
            for m in _CMD.finditer(line):
                # A nested `$(uv run python -m gtm_core.x --flag)` is its OWN citation — the
                # regex finds it separately — so its flags must not be charged to the outer
                # command, which does not declare them.
                rest = _trim(line, m)
                subs = _subcommands(rest)
                where = f"{doc.parent.name}/{doc.name}"
                if doc.parent.name == "references":
                    where = f"{doc.parent.parent.name}/references/{doc.name}"
                found.append(
                    (m.group(1), subs, tuple(sorted(set(_FLAG.findall(rest)))), in_fence, where)
                )
    return found


#: `gtm_core.prospects adjudication write-verdicts` is a TWO-level dispatcher. Reading only the
#: first token reports every flag of the real leaf command as unknown — a false positive that
#: would have had someone "fix" nine correct skills.
_MAX_DEPTH = 2


def _subcommands(rest: str) -> tuple[str, ...]:
    """The bare words of a command line — its subcommand chain, at most two deep.

    A global option may legally precede the subcommand (``elements --profile P set <slug>``), so
    this skips flags and the value each one consumes rather than stopping at the first ``-``.
    Reading only LEADING words reports `set`'s own flags against the top-level parser, which is
    a false positive dressed as a broken skill."""
    out: list[str] = []
    skip = False
    for tok in rest.split():
        if skip:
            skip = False
            continue
        if tok.startswith("-"):
            skip = True  # assume it takes a value; a flag that doesn't costs us one candidate
            continue
        if _SUB.fullmatch(tok):
            out.append(tok)
            if len(out) == _MAX_DEPTH:
                break
    return tuple(out)


@cache
def _help(module: str, subs: tuple[str, ...]) -> tuple[int, str]:
    argv = [sys.executable, "-m", module, *subs, "--help"]
    p = subprocess.run(argv, cwd=REPO, capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout + p.stderr


def _deepest_real(module: str, subs: tuple[str, ...]) -> tuple[tuple[str, ...], int, str]:
    """The longest prefix of ``subs`` the parser actually accepts, with its help text.

    A leading word may be a subcommand OR a positional value (`slugify "Acme Ltd"`), so the
    chain is discovered by asking the parser, never assumed from the text."""
    rejected = False
    while True:
        rc, text = _help(module, subs)
        if rc == 0 or not subs:
            return subs, (2 if rc == 0 and rejected else rc), text
        rejected = rejected or bool(re.search(r"invalid choice|No such command", text))
        subs = subs[:-1]


def _required_flags(help_text: str) -> set[str]:
    usage = help_text.split("\n\n", 1)[0]
    while True:
        stripped = re.sub(r"\[[^\[\]]*\]", " ", usage)
        if stripped == usage:
            break
        usage = stripped
    return set(_FLAG.findall(usage))


def _problems(
    module: str, subs: tuple[str, ...], flags: tuple[str, ...], fenced: bool
) -> list[str]:
    real, rc, text = _deepest_real(module, subs)
    if rc != 0:
        if subs and re.search(r"invalid choice|No such command", text):
            return [f"subcommand-rejected:{' '.join(subs)}"]
        # Not every CLI is argparse: `check_env` ignores `--help` and reports on the environment.
        # A tool we cannot interrogate is UNVERIFIABLE, not broken — say so only when the citation
        # actually claims flags, because that is the only claim this gate could have checked.
        return [f"help-unavailable:{rc}"] if flags else []
    # A global flag (`--profile`) is often declared on a parent, so the surface is every depth.
    surface = "".join(_help(module, real[:d])[1] for d in range(len(real) + 1))
    out = [
        f"unknown:{f}"
        for f in flags
        if f != "--help" and not re.search(re.escape(f) + r"(?![\w-])", surface)
    ]
    if fenced:
        out += [f"missing-required:{f}" for f in sorted(_required_flags(text) - set(flags))]
    return out


def _cases():
    seen: dict[tuple, str] = {}
    for module, subs, flags, fenced, doc in _citations():
        seen.setdefault((module, subs, flags, fenced), doc)
    for (module, subs, flags, fenced), doc in sorted(
        seen.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])
    ):
        name = " ".join((module.removeprefix("gtm_core."), *subs))
        yield pytest.param(
            module, subs, flags, fenced, id=f"{doc}::{name}{'[fenced]' if fenced else ''}"
        )


def test_the_extractor_finds_the_skill_commands() -> None:
    """Negative control on the instrument: an extractor that silently finds nothing passes everything."""
    modules = {c[0] for c in _citations()}
    assert len(modules) >= 40, (
        f"extractor found only {len(modules)} cited modules — it is broken, not the skill"
    )
    for expected in (
        "gtm_core.paths",
        "gtm_core.preflight",
        "gtm_core.funnel",
        "gtm_core.prospects_consolidate",
    ):
        assert expected in modules


def test_a_nested_command_substitution_is_not_charged_to_the_outer_command() -> None:
    """`suppression verify --ledger "$(... paths --profile X --name Y)"` cites TWO commands; the
    inner one's flags belong to the inner parser. Reported as outer-command defects, they read as
    nine broken skills that are in fact correct."""
    line = 'python -m gtm_core.a verify --ledger "$(python -m gtm_core.b paths --profile p)"'
    (outer,) = _CMD.finditer(line)  # the match is greedy: it runs through the inner command too
    assert _FLAG.findall(_trim(line, outer)) == ["--ledger"]


def test_a_flag_after_the_substitution_still_belongs_to_the_outer_command() -> None:
    """The regression my own first fix caused: cutting at `$(` loses everything after `)`."""
    line = 'python -m gtm_core.a verify --ledger "$(python -m gtm_core.b paths -p x)" --target c'
    (outer,) = _CMD.finditer(line)
    assert _FLAG.findall(_trim(line, outer)) == ["--ledger", "--target"]


def test_an_inner_command_does_not_swallow_the_outer_ones_later_flags() -> None:
    """The other direction, and the one that reported `email-sequence` as broken: a `$(...)` that
    OPENS before the match. The inner command ends at its `)`; `--sequence-id` after it belongs to
    the outer command, and charged inward it reads as a flag `resolve_knowledge` does not have."""
    line = '--hook "$(python -m gtm_core.b f.md --profile <active>)" --sequence-id <x> --json o'
    (inner,) = _CMD.finditer(line)
    assert _FLAG.findall(_trim(line, inner)) == ["--profile"]


def test_a_two_level_subcommand_is_read_to_its_leaf() -> None:
    assert _subcommands(" adjudication write-verdicts --csv x.csv") == (
        "adjudication",
        "write-verdicts",
    )
    assert _subcommands(" <slug>.shots.json --render-shotlist") == ()
    # A global option before the subcommand must not hide it.
    assert _subcommands(" --profile <active> set <slug> --handle x=<id>") == ("set",)


def test_a_pipe_inside_a_placeholder_does_not_end_the_command() -> None:
    line = "python -m gtm_core.web_sweep normalize --segment <enterprise|startup> --hits h.json | tee out"
    (m,) = _CMD.finditer(line)
    assert _FLAG.findall(m.group(2)) == ["--segment", "--hits"]


def test_an_enum_value_does_not_end_the_command_but_a_shell_pipe_does() -> None:
    """`--kind character|environment --name N`: stopping at the bare pipe hides `--name`, and the
    command then reads as omitting a flag it plainly cites."""
    line = "python -m gtm_core.elements define <slug> --kind character|environment --name x | tee f"
    (m,) = _CMD.finditer(line)
    assert _FLAG.findall(m.group(2)) == ["--kind", "--name"]


def test_the_required_flag_parser_discriminates() -> None:
    usage = "usage: x [-h] --company COMPANY --hits HITS [--segment SEGMENT] [--as-of AS_OF [--deep]]\n\noptions:\n  --segment"
    assert _required_flags(usage) == {"--company", "--hits"}


@pytest.mark.parametrize(("module", "subs", "flags", "fenced"), list(_cases()))
def test_cited_command_parses(
    module: str, subs: tuple[str, ...], flags: tuple[str, ...], fenced: bool
) -> None:
    problems = _problems(module, subs, flags, fenced)
    known = [p for p in problems if (module, subs, p) in KNOWN_DEFECTS]
    fresh = [p for p in problems if (module, subs, p) not in KNOWN_DEFECTS]
    assert not fresh, f"`python -m {module} {' '.join(subs)}` as cited {flags}: {fresh}"
    if known:
        pytest.xfail("; ".join(KNOWN_DEFECTS[(module, subs, p)] for p in known))


def test_known_defects_are_still_defects() -> None:
    """A KNOWN_DEFECTS entry that no longer reproduces must be deleted, not left to rot."""
    live = set()
    for module, subs, flags, fenced, _doc in _citations():
        live.update((module, subs, p) for p in _problems(module, subs, flags, fenced))
    stale = [k for k in KNOWN_DEFECTS if k not in live]
    assert not stale, f"fixed — remove from KNOWN_DEFECTS: {stale}"
