"""P0-2 — every command a shipped skill documents must pass the runtime classifier.

The 0.11.1 class of failure was skills documenting commands the least-privilege policy
denies at runtime — found in production by a failing run, because nothing at commit time
proved the two agreed. This gate closes that: it extracts every fenced shell block from
every *generated* ``plugin/skills/*/SKILL.md`` (the bytes that actually ship), classifies
each command line with :func:`agent.permissions.classify_tool`, and fails on any
``deny``/``escalate``.

A fence that is genuinely for a human terminal (an operator-run fallback, laptop-only
setup) opts out by tagging its info string::

    ```bash interactive-only

Blockquoted fences (``> ```bash``) are supported — the blockquote prefix is stripped from
body lines. SKILL.md is generated: fix the command in the skill's ``body_template.md``
and re-run ``python -m gtm_core.skills.codegen generate-all``, never the SKILL.md itself.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent.permissions import classify_tool

REPO = Path(__file__).resolve().parents[2]
SKILL_FILES = sorted((REPO / "plugin" / "skills").glob("*/SKILL.md"))

#: Fence languages treated as runnable shell. Untagged fences are ignored — they are
#: output samples, sentinels, or prose, not commands the brain is told to run.
SHELL_LANGS = {"bash", "sh", "shell", "zsh", "console"}

_FENCE_RE = re.compile(r"^(?P<quote>(?:>\s?)*)```(?P<info>.*)$")
_QUOTE_RE = re.compile(r"^(?:>\s?)+")
_HEREDOC_RE = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?")


def _shell_blocks(text: str):
    """Yield ``(first_body_line_no, body_lines)`` for each classifiable shell fence."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        opened = _FENCE_RE.match(lines[i])
        if opened is None:
            i += 1
            continue
        info = opened.group("info").strip()
        parts = info.lower().split()
        lang = parts[0] if parts else ""
        body: list[str] = []
        start = i + 2  # 1-based line number of the first body line
        i += 1
        while i < len(lines):
            closer = _FENCE_RE.match(lines[i])
            if closer is not None and closer.group("info").strip() == "":
                break
            body.append(lines[i])
            i += 1
        i += 1  # past the closing fence
        if lang not in SHELL_LANGS or "interactive-only" in parts[1:]:
            continue
        if opened.group("quote"):
            body = [_QUOTE_RE.sub("", ln, count=1) for ln in body]
        yield start, body


def _unclosed_quote(s: str) -> bool:
    """True when ``s`` ends inside an open single/double quote (shell semantics)."""
    in_single = in_double = escaped = False
    for ch in s:
        if escaped:
            escaped = False
            continue
        if ch == "\\" and not in_single:
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
    return in_single or in_double


def _commands(body: list[str], start: int):
    """Yield ``(line_no, command)`` — one *logical* command each.

    Comments/blanks skipped, leading ``$`` prompts stripped, heredoc bodies skipped.
    Two continuation forms are joined, because the brain issues them as ONE Bash call
    and the runtime classifier sees them whole: a trailing ``\\`` (joined with a space,
    mirroring the classifier's own continuation handling) and an **open quote** spanning
    lines — the documented multi-line ``--json '{…}'`` convention (joined with a newline;
    the classifier's segment splitter is quote-aware, so an embedded newline inside
    quotes does not split the command)."""

    def _complete(cmd: str) -> bool:
        return not cmd.endswith("\\") and not _unclosed_quote(cmd)

    heredoc: str | None = None
    buf = ""
    buf_line = 0
    for offset, raw in enumerate(body):
        line_no = start + offset
        if heredoc is not None:
            if raw.strip() == heredoc:
                heredoc = None
            continue
        stripped = raw.strip()
        if buf:
            if buf.endswith("\\"):
                buf = buf[:-1].rstrip() + " " + stripped
            else:  # open quote spans lines — preserve the newline
                buf += "\n" + stripped
            if _complete(buf):
                yield buf_line, buf
                buf = ""
            continue
        if not stripped or stripped.startswith("#"):
            continue
        stripped = re.sub(r"^\$\s+", "", stripped)
        here = _HEREDOC_RE.search(stripped)
        if here is not None and not _unclosed_quote(stripped):
            heredoc = here.group(1)
            yield line_no, stripped  # the heredoc *opener* is itself classifiable
            continue
        if _complete(stripped):
            yield line_no, stripped
        else:
            buf = stripped
            buf_line = line_no
    if buf:
        yield buf_line, buf


def test_every_documented_skill_command_passes_the_classifier():
    assert SKILL_FILES, "no shipped skills found under plugin/skills/ — wrong layout?"
    violations: list[str] = []
    for path in SKILL_FILES:
        text = path.read_text(encoding="utf-8")
        for start, body in _shell_blocks(text):
            for line_no, cmd in _commands(body, start):
                decision = classify_tool("Bash", {"command": cmd})
                if decision != "allow":
                    rel = path.relative_to(REPO)
                    violations.append(f"{rel}:{line_no}: [{decision}] {cmd}")
    assert not violations, (
        "Shipped skills document commands the least-privilege policy blocks at runtime\n"
        "(agent/permissions.py). Fix the command in the skill's body_template.md (approved\n"
        "routes: MCP renderer, `python -m …`, one command per Bash call) and regenerate via\n"
        "`python -m gtm_core.skills.codegen generate-all` — or, ONLY for a genuinely\n"
        "human-run fence, tag it ```bash interactive-only. Violations:\n  "
        + "\n  ".join(violations)
    )
