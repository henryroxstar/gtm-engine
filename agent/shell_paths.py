"""The places a shell command reads and writes, for the per-tenant path policy (MM9, first layer).

``agent.path_confinement`` decides WHERE a run may go; this module works out where a ``Bash`` command
is trying to. It tokenises the way the shell does (quotes, ``>``/``<`` glued to a word, ``2>&1``, heredoc
bodies), pulls the redirects out, models the common file-reading and file-writing programs, and checks
every place it can name statically against the run's roots.

What it can and cannot know, stated so nobody reads more into it than is there:

* it KNOWS a path written in the command (absolute, ``~``, climbing, or containing a ``/``), and a glob's
  fixed prefix; it refuses a path the shell would compute (``$VAR``, a command substitution handed to a
  program that takes files) because the answer does not exist until the command runs;
* it does NOT know what a program does with a bare name it opens itself — ``python script.py`` runs
  whatever ``script.py`` says, and an inline interpreter (``perl -e``, ``awk 'BEGIN{getline<"…"}'``)
  carries its paths inside a string. That class is the job of an OS-level second layer
  (a per-run operating-system user); nothing here pretends otherwise.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from .path_confinement import UNRESOLVABLE, Confinement, check_read, check_write, resolve

_PUNCT = "<>|&;()"
_NUMERIC = re.compile(r"^[+-]?\d+(\.\d+)?[kKmMgG]?$")
_ASSIGN = re.compile(r"^[A-Za-z_]\w*=")

# Programs whose operands are all files they read.
_BARE_READ = frozenset(
    "cat head tail less more wc sort uniq cut diff cmp file stat xxd od strings base64 nl tac rev "
    "paste comm fold fmt column md5sum sha256sum shasum realpath readlink source . ls tree du "
    "zcat gzcat bzcat xzcat zipinfo hexdump hd iconv".split()
)
# Programs whose FIRST operand is a pattern or script (unless -e/-f/--expression/--file/--files says
# otherwise), the rest files.
_PATTERN_FIRST = frozenset("grep egrep fgrep rg ag sed awk gawk jq".split())
# Programs that create, change or remove files: every operand is a destination.
_WRITE_ALL = frozenset(
    "mkdir touch tee truncate rmdir mv chmod chown chgrp unlink shred dd split tar unzip gunzip "
    "bunzip2 unxz".split()
)
# Programs that copy: the LAST operand is the destination (or -t DIR), the others are read.
_COPY = frozenset("cp rsync install ln".split())
# Programs that only print their arguments: nothing they are handed is opened.
_PRINT = frozenset("echo printf true false date pwd uname whoami id sleep seq expr test [".split())
# Wrappers that run another program. A wrapper OPTION can change the cwd or read a file (env -C, xargs -a),
# and xargs takes its operands from stdin — neither can be checked, so both are refused.
_WRAPPERS = frozenset(
    "env xargs nice nohup time timeout command exec stdbuf setsid ionice builtin".split()
)
_IMPLICIT_CWD = frozenset({"ls", "tree", "du"})
_SINKS = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr"})

_FD_DUP = re.compile(r"\d*>&(?:\d+|-)")  # 2>&1, >&2, 2>&-: stream plumbing, not a place
_FD_PREFIX = re.compile(r"(?<![\w\-])\d+(?=[<>])")  # the 2 in 2>file
_HEREDOC = re.compile(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")


def _strip_heredocs(command: str) -> str:
    """Drop heredoc BODIES: they are data written to a file or fed to a program, not commands, and
    would otherwise be judged line by line as if each were one."""
    out, pos = [], 0
    while m := _HEREDOC.search(command, pos):
        line_end = command.find("\n", m.end())
        if line_end == -1:
            out.append(command[pos:])
            return "".join(out)
        end = re.compile(rf"^[ \t]*{re.escape(m.group(2))}[ \t]*$", re.M).search(command, line_end)
        out.append(command[pos : line_end + 1])
        pos = end.end() if end else len(command)
    out.append(command[pos:])
    return "".join(out)


def _normalise(command: str) -> str:
    command = re.sub(r"\\\r?\n", " ", command)
    command = _strip_heredocs(command)
    command = _FD_DUP.sub(" ", command)
    command = command.replace("&>>", ">>").replace("&>", ">")
    return _FD_PREFIX.sub("", command)


_SUBST = "$SUBST"  # what a command substitution leaves in the outer command: unresolvable by design


def _extract_substitutions(command: str) -> tuple[str, list[str]]:
    """``(outer command with each substitution replaced by a placeholder, the inner commands)``.

    The placeholder contains ``$``, so as an operand of a program that takes files it cannot be resolved
    and the command is refused; as an operand of ``echo`` or a script it is never checked, and the inner
    command is judged on its own. Single quotes suppress substitution, as in the shell; double quotes do not.
    """
    outer: list[str] = []
    inner: list[str] = []
    i, n, single = 0, len(command), False
    while i < n:
        ch = command[i]
        if ch == "'":
            single = not single
        elif not single and ch == "`":
            end = command.find("`", i + 1)
            end = n if end == -1 else end
            inner.append(command[i + 1 : end])
            outer.append(_SUBST)
            i = end + 1
            continue
        elif not single and ch == "$" and command[i + 1 : i + 2] == "(":
            depth, j = 1, i + 2
            while j < n and depth:
                depth += (command[j] == "(") - (command[j] == ")")
                j += 1
            inner.append(command[i + 2 : j - 1])
            outer.append(_SUBST)
            i = j
            continue
        outer.append(ch)
        i += 1
    return "".join(outer), inner


def _tokens(segment: str) -> list[str] | None:
    lex = shlex.shlex(segment, posix=True, punctuation_chars=_PUNCT)
    lex.whitespace_split = True
    lex.commenters = (
        ""  # bash starts a comment only at the start of a word, and shlex does not know
    )
    try:
        return list(lex)
    except ValueError:
        return None


def _is_punct(tok: str) -> bool:
    return bool(tok) and all(ch in _PUNCT for ch in tok)


def _split_redirects(tokens: list[str]) -> tuple[list[str], list[str], list[str]]:
    """``(words, read targets, write targets)``: redirects come out wherever they appear — after the
    command, before it, or glued to a word — leaving only the words."""
    words: list[str] = []
    reads: list[str] = []
    writes: list[str] = []
    it = iter(tokens)
    for tok in it:
        if not _is_punct(tok):
            words.append(tok)
        elif tok.startswith(">"):
            writes.append(next(it, ""))
        elif tok == "<":
            reads.append(next(it, ""))
        elif tok in ("<<", "<<<", "<&"):
            next(it, None)  # a heredoc delimiter, a here-string, or an fd: data, not a place
        # anything else (| ; & ( )) only separates commands: the caller already split on those
    return words, reads, writes


def _looks_like_path(tok: str) -> bool:
    if not tok or any(ch.isspace() for ch in tok) or "://" in tok or tok.startswith("-"):
        return False
    return "/" in tok or tok.startswith(("~", "./", "../")) or tok in (".", "..")


def _is_strict_path(tok: str) -> bool:
    """A token that names a place on its face — absolute, home-relative, or climbing."""
    return (
        tok.startswith(("/", "~", "./", "../"))
        or tok in (".", "..")
        or "/../" in tok
        or tok.endswith("/..")
    )


# options that take a value, by program class: (flag -> role of its value)
_PATTERN_OPTS = {
    "-e": "pattern",
    "--regexp": "pattern",
    "--expression": "pattern",
    "-f": "file",
    "--file": "file",
    "--from-file": "file",
}
# Options of the pattern-first programs that take a separate VALUE (how many tokens each consumes): that
# value is not an operand, so it must not be mistaken for the pattern or for a file.
_VALUE_OPTS = {
    "-m": 1, "-A": 1, "-B": 1, "-C": 1, "-d": 1, "-D": 1, "-g": 1, "-t": 1, "-T": 1, "-j": 1,
    "-F": 1, "-v": 1, "--max-count": 1, "--context": 1, "--after-context": 1,
    "--before-context": 1, "--label": 1, "--include": 1, "--exclude": 1, "--exclude-dir": 1,
    "--glob": 1, "--type": 1, "--threads": 1, "--arg": 2, "--argjson": 2,
}  # fmt: skip


def _pattern_option(tok: str, it, reads: list[str]) -> tuple[bool, bool]:
    """One option of a pattern-first program: ``(pattern given, in-place)``.

    Consumes the option's separate value from ``it`` (a pattern, a script file, a count, a glob): that
    value is not an operand, so it must be taken out before the first remaining operand is read as the
    pattern. A pattern or script FILE (``-f FILE``) is something the run reads, so it joins ``reads``.
    """
    name, eq, value = tok.partition("=")
    given = False
    if name == "--files":
        given = True
    elif name in _PATTERN_OPTS:  # -e PAT / --regexp=PAT / -f FILE / --file=FILE
        val = value if eq else next(it, "")
        if _PATTERN_OPTS[name] == "file":
            reads.append(val)
        given = True
    elif len(tok) > 2 and tok[1] in "ef" and not tok.startswith("--"):
        given = True  # -efoo / -fFILE glued
        if tok[1] == "f":
            reads.append(tok[2:])
    elif name in _VALUE_OPTS and not eq:
        for _ in range(_VALUE_OPTS[name]):
            next(it, None)
    in_place = name == "--in-place" or (not tok.startswith("--") and "i" in tok[1:])
    return given, in_place


def _parse(
    args: list[str], base: str
) -> tuple[list[str], list[str], list[str], list[str], bool, bool]:
    """``(operands, flags, extra reads, extra writes, pattern given, in-place)`` of one command's args."""
    operands: list[str] = []
    flags: list[str] = []
    reads: list[str] = []
    writes: list[str] = []
    pattern_given = inplace = False
    it = iter(args)
    for tok in it:
        if tok == "-":
            continue  # stdin/stdout, not a place
        if not tok.startswith("-"):
            operands.append(tok)
            continue
        flags.append(tok)
        name, eq, value = tok.partition("=")
        if base in _PATTERN_FIRST:
            given, in_place = _pattern_option(tok, it, reads)
            pattern_given = pattern_given or given
            inplace = inplace or (base == "sed" and in_place)
        elif base == "sort" and name in ("-o", "--output"):
            writes.append(value if eq else next(it, ""))
        elif base in _COPY | {"mv"} and name in ("-t", "--target-directory"):
            writes.append(value if eq else next(it, ""))
        elif eq and name.startswith("--") and _is_strict_path(value):
            (writes if base in _WRITE_ALL | _COPY else reads).append(value)
    return operands, flags, reads, writes, pattern_given, inplace


def _find_places(args: list[str]) -> tuple[list[str], list[str], list[str]]:
    """``(start paths, strict-checked tokens, write targets)`` of a ``find`` command line."""
    paths: list[str] = []
    for tok in args:
        if tok.startswith(("-", "(", "!")):
            break
        paths.append(tok)
    rest = args[len(paths) :]
    writes: list[str] = []
    exec_args: list[str] = []
    for i, tok in enumerate(rest):
        if tok in ("-fprint", "-fprint0", "-fprintf", "-fls") and i + 1 < len(rest):
            writes.append(rest[i + 1])
        elif tok == "-delete":
            writes += (
                paths  # it removes what it finds: the start paths must be somewhere it may write
            )
        elif tok in ("-exec", "-execdir", "-ok", "-okdir"):
            exec_args += rest[i + 1 :]
    return paths, [t for t in exec_args if _is_strict_path(t)], writes


def _roles(base, operands, flags, reads, writes, pattern_given, inplace, args):
    """Sort operands into files read (returned) and files written (appended to ``writes``/``reads``)."""
    if base in _PATTERN_FIRST:
        files = [t for t in (operands if pattern_given else operands[1:]) if not _NUMERIC.match(t)]
        if inplace:
            writes += files
            return []
        return files
    if base in _WRITE_ALL:
        writes += [t for t in operands if not _NUMERIC.match(t)]
        return []
    if base in _COPY | {"mv"}:
        if operands and not writes:
            writes.append(operands[-1])
            operands = operands[:-1]
        reads += operands
        return []
    if base in _BARE_READ:
        return [t for t in operands if not _NUMERIC.match(t)]
    if base in _PRINT:
        return []
    strict_only = base == "git"  # `git log origin/main`: a slash in a ref is not a place
    return [t for t in operands if (_is_strict_path(t) if strict_only else _looks_like_path(t))]


def _recursive(base: str, flags: list[str]) -> bool:
    if base in ("rg", "ag"):
        return True
    if base not in ("grep", "egrep", "fgrep"):
        return False
    return any(
        f in ("--recursive", "-r", "-R") or (f[:2] != "--" and set(f[1:]) & {"r", "R"})
        for f in flags
    )


_DECLARERS = frozenset({"export", "declare", "typeset", "readonly", "local"})


def _assignment(word: str, reads: list, writes: list) -> None:
    """``NAME=value`` before or after ``export``: a value that looks like a place is one, read like any operand.

    ``GTM_CONTENT_ROOT=/elsewhere prog`` points every tool the program starts at /elsewhere, so a value that looks
    like a place is checked. GTM_CONTENT_ROOT is where those tools WRITE, so it must be somewhere the run may
    write, not merely read. (A flag, a number or a word is not a place.)
    """
    name, value = word.split("=", 1)
    if value[:1] in ("/", "~", ".", "$", "`") or "/" in value:
        reads.append(value)
        if name == "GTM_CONTENT_ROOT":
            writes.append(value)


def _strip_assignments(words: list, reads: list, writes: list) -> list:
    """Drop the leading ``NAME=value`` words (checking each), and an ``export``-style declaration entirely.

    ``export GTM_CONTENT_ROOT=/elsewhere`` sets the same variable for the next command as a prefix would, so its
    assignments get the same check; the declaration itself names no file, so nothing of it is left to check.
    """
    while words and _ASSIGN.match(words[0]):
        _assignment(words.pop(0), reads, writes)
    if words and words[0] in _DECLARERS:
        for word in words[1:]:
            if _ASSIGN.match(word):
                _assignment(word, reads, writes)
        return []
    return words


def _words(words, reads, writes, c: Confinement, cwd: Path):
    """Check one command's words and redirects. Returns ``(refusal | None, cwd after it)``."""
    words = list(words)
    words = _strip_assignments(words, reads, writes)
    files: list[str] = []
    if words:
        base = words[0].rsplit("/", 1)[-1]
        args = words[1:]
        if base in _WRAPPERS:
            if base == "xargs" or (args and args[0].startswith("-")):
                return (UNRESOLVABLE, f"{base} with options or stdin operands"), cwd
            if base == "timeout" and args and _NUMERIC.match(args[0]):
                args = args[1:]
            return _words(args, reads, writes, c, cwd)
        if base == "cd":
            target = args[0] if args else "~"
            return check_read(target, c, cwd), (resolve(target, cwd) or cwd)
        if base == "find":
            paths, strict, found = _find_places(args)
            files = [*paths, *strict]
            writes += found
        else:
            operands, flags, r, w, pattern_given, inplace = _parse(args, base)
            reads += r
            writes += w
            files = _roles(base, operands, flags, reads, writes, pattern_given, inplace, args)
            walks = base in _IMPLICIT_CWD or _recursive(base, flags)
            if walks and not files and not reads:
                files = [
                    "."
                ]  # no operand: it walks the cwd — on the backend, the whole shared checkout
    for tok in (*files, *reads):
        if bad := check_read(tok, c, cwd):
            return bad, cwd
    for tok in writes:
        if tok not in _SINKS and (bad := check_write(tok, c, cwd)):
            return bad, cwd
    return None, cwd


def bash_refusal(command: str, c: Confinement) -> tuple[str, str] | None:
    """``(reason, offending text)`` when this command leaves the workspace, else ``None``."""
    from .permissions import _split_segments

    pending = [_normalise(command)]
    cwd = c.cwd
    while pending:
        outer, inner = _extract_substitutions(pending.pop(0))
        pending += [_normalise(i) for i in inner]
        for raw in _split_segments(outer):
            seg = raw.strip()
            if not seg:
                continue
            tokens = _tokens(seg)
            if tokens is None:
                return (UNRESOLVABLE, seg[:80])  # unbalanced quotes: nothing to check, so refuse
            words, reads, writes = _split_redirects(tokens)
            bad, cwd = _words(words, reads, writes, c, cwd)
            if bad:
                return bad
    return None
