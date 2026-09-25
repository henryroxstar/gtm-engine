"""Refuse pipeline vocabulary in text an OPERATOR — not a developer — is meant to read.

``PROSPECTING.md`` is explicit that the operator's job is written to a person, in the
operator's own words, never the procedure's internal nouns: ``lane``, ``verdict``,
``.jsonl``, an MCP tool name. Those words are exactly right in code, docs-for-Claude, and
a skill's own body — and exactly wrong the moment they reach a status line, a report
caption, or a sheet a human reads without this repo's vocabulary in their head. PS7/PS8's
own ``gtm_core.prospect_status`` module exists to translate lane/trigger jargon into six
plain words for that reason; this lint is the tripwire that a translation stays a
translation instead of leaking its source vocabulary back through.

**Scope for this pass.** Two sources are scanned today: (1) ``gtm_core.prospect_status``'s
own ``LABELS``/``NEXT_STEP`` values, proving the module this wave adds is itself clean,
and (2) whatever the caller hands in — a raw string, or a set of file globs under a root.
A later wave (Track F) will add real skill-body content delimited by
``<!-- operator --> ... <!-- /operator -->`` markers; that markup doesn't exist in any
file yet, so this module does not hardcode a search for it in a place it can't find it.
Instead :func:`findings` degrades gracefully: a scanned file WITH markers is checked only
inside them (code/dev-only prose around the markers is not operator-facing and stays
un-linted); a scanned file with none is checked WHOLE, so a file written before the
markers exist is still covered rather than silently skipped.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Plain-word/identifier tokens, case-insensitive, word-boundary matched. Populated at
#: import time with the twelve hold triggers and four exclude triggers so a trigger added
#: or renamed in ``gtm_core/lanes/model.py`` is picked up here without a hand-edit.
_BASE_WORD_TOKENS: tuple[str, ...] = (
    "lane",
    "lanes",
    "verdict",
    "re-angle",
    "MASTER_COLS",
    "lane_reason",
    "signal_clause",
    "gtm_core",
    # 2026-09-23. "gated" was used in one operator-facing report for BOTH *passed the
    # checks* and *held by them* — opposite meanings, one word, and the reader could not
    # tell which had happened. The checks pass or they refuse; those are the two words.
    "gated",
)


def _word_tokens() -> tuple[str, ...]:
    from gtm_core.lanes.model import EXCLUDE_ORDER, HOLD_ORDER

    return (*_BASE_WORD_TOKENS, *HOLD_ORDER, *EXCLUDE_ORDER)


def _compile_patterns() -> tuple[re.Pattern, ...]:
    # Longest-first so a multi-word trigger like `tier-a-generic` is tried before any
    # token that could otherwise short-circuit inside it (belt-and-suspenders: `\b...\b`
    # already prevents a partial-word match on its own).
    words = sorted(_word_tokens(), key=len, reverse=True)
    word_pattern = re.compile(
        r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE
    )
    ext_pattern = re.compile(r"\.(?:csv|jsonl)\b", re.IGNORECASE)
    mcp_pattern = re.compile(r"\bmcp__", re.IGNORECASE)
    # The identity-key shape `a:<value>`, `d:<value>`, `c:<value>` (gtm_core.prospects_state
    # / gtm_core.lanes.router.account_key) — not case-folded, since these are literal
    # single-letter prefixes, not English words.
    idkey_pattern = re.compile(r"\b[adc]:\S")
    return (word_pattern, ext_pattern, mcp_pattern, idkey_pattern)


_OPEN_MARKER_RE = re.compile(r"<!--\s*operator\s*-->", re.IGNORECASE)
_CLOSE_MARKER_RE = re.compile(r"<!--\s*/\s*operator\s*-->", re.IGNORECASE)
_NEAR_MISS_RE = re.compile(r"<!--\s*/?\s*operat[a-z0-9_-]*\s*-->", re.IGNORECASE)
_MARKER_RE = re.compile(
    r"<!--\s*operator\s*-->(.*?)<!--\s*/operator\s*-->", re.DOTALL | re.IGNORECASE
)

#: Operator-facing files scanned by default when no --glob is passed
DEFAULT_GLOBS: tuple[str, ...] = (
    "plugin/skills/prospect/body_template.md",
    "plugin/skills/email-sequence/body_template.md",
    "plugin/skills/prospect/SKILL.md",
    "plugin/skills/email-sequence/SKILL.md",
    "gtm_core/enrollment_gate.py",
    "gtm_core/prospect_guards.py",
    "gtm_core/account_folder.py",
    "gtm_core/preflight_report.py",
)


def _scan(source: str, text: str, start_lineno: int = 1) -> list[tuple[str, int, str, str]]:
    """``(source, lineno, token, sentence)`` for every banned token in ``text``."""
    out: list[tuple[str, int, str, str]] = []
    patterns = _compile_patterns()
    for offset, line in enumerate(text.splitlines() or [text]):
        lineno = start_lineno + offset
        for pattern in patterns:
            for m in pattern.finditer(line):
                ctx = " ".join(line[max(0, m.start() - 40) : m.end() + 40].split())
                out.append((source, lineno, m.group(0), ctx))
    return out


def _scan_text(source: str, raw: str) -> list[tuple[str, int, str, str]]:  # noqa: C901 — one linear scan over the marker grammar
    """Scan raw text, validating operator markers and checking contents."""
    out: list[tuple[str, int, str, str]] = []

    # Check for near-miss markers
    for m in _NEAR_MISS_RE.finditer(raw):
        s = m.group(0)
        if not (_OPEN_MARKER_RE.fullmatch(s) or _CLOSE_MARKER_RE.fullmatch(s)):
            lineno = raw[: m.start()].count("\n") + 1
            out.append((source, lineno, s, f"near-miss operator marker: {s!r}"))

    open_matches = list(_OPEN_MARKER_RE.finditer(raw))
    close_matches = list(_CLOSE_MARKER_RE.finditer(raw))
    if len(open_matches) != len(close_matches):
        lineno = raw[: open_matches[-1].start()].count("\n") + 1 if open_matches else 1
        out.append(
            (
                source,
                lineno,
                "unbalanced-markers",
                f"unbalanced operator markers: {len(open_matches)} open vs {len(close_matches)} close",
            )
        )
        return out

    if open_matches:
        for m in _MARKER_RE.finditer(raw):
            block_start = m.start(1)
            start_lineno = raw[:block_start].count("\n") + 1
            block_text = m.group(1)
            out += _scan(source, block_text, start_lineno=start_lineno)
    elif source.endswith(".py"):
        import ast

        try:
            tree = ast.parse(raw, filename=source)
        except SyntaxError:
            return out

        def _get_str(node: ast.AST) -> str | None:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.JoinedStr):
                parts = []
                for val in node.values:
                    if isinstance(val, ast.Constant) and isinstance(val.value, str):
                        parts.append(val.value)
                return "".join(parts) if parts else None
            return None

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = ""
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name == "Refusal":
                    for arg in node.args[:5]:  # what, why, next_step, alternative, cost
                        s = _get_str(arg)
                        if s:
                            out += _scan(
                                source, s, start_lineno=getattr(arg, "lineno", node.lineno)
                            )
                    for kw in node.keywords:
                        if kw.arg in {"what", "why", "next_step", "alternative", "cost"}:
                            s = _get_str(kw.value)
                            if s:
                                out += _scan(
                                    source, s, start_lineno=getattr(kw.value, "lineno", node.lineno)
                                )
    else:
        out += _scan(source, raw, start_lineno=1)
    return out


def _scan_prospect_status() -> list[tuple[str, int, str, str]]:
    from gtm_core.prospect_status import LABELS, NEXT_STEP

    out: list[tuple[str, int, str, str]] = []
    for name, mapping in (("LABELS", LABELS), ("NEXT_STEP", NEXT_STEP)):
        for key, value in mapping.items():
            out += _scan(f"gtm_core.prospect_status.{name}[{key!r}]", value)
    return out


def findings(
    text_or_root: Path | str | None = None,
    *,
    root: Path | None = None,
    globs: Sequence[str] = (),
    text: str | None = None,
) -> list[tuple[str, int, str, str]]:
    """``(source, lineno, token, sentence)`` for every banned token found.

    Always scans ``gtm_core.prospect_status``'s own ``LABELS``/``NEXT_STEP`` values.
    Additionally scans ``text`` if given (a raw string — the caller-supplied source for
    testing), and every file matching each pattern in ``globs`` under ``root`` (default:
    repo root) if given. ``globs``/``text`` are independent and additive; passing neither
    scans only this module's one built-in source.
    """
    if isinstance(text_or_root, str):
        is_dir = False
        if len(text_or_root) < 256:
            try:
                is_dir = Path(text_or_root).is_dir()
            except OSError:
                is_dir = False
        if globs or (root is None and is_dir):
            root = Path(text_or_root)
        else:
            text = text_or_root if text is None else text
    elif isinstance(text_or_root, Path):
        root = text_or_root

    out = _scan_prospect_status()
    if text is not None:
        out += _scan_text("<text>", text)
    if globs:
        base = root or ROOT
        for pattern in globs:
            for f in sorted(base.glob(pattern)):
                if not f.is_file():
                    continue
                out += _scan_text(str(f.relative_to(base)), f.read_text(encoding="utf-8"))
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="tests.lint.operator_vocabulary",
        description="Refuse pipeline vocabulary (lane/verdict/.jsonl/mcp__/...) in operator-facing text.",
    )
    p.add_argument(
        "--glob",
        action="append",
        default=[],
        dest="globs",
        help="additional glob (relative to --root) of operator-facing files to scan; repeatable",
    )
    p.add_argument(
        "--root",
        default=None,
        help="root the --glob patterns are relative to (default: repo root; mainly for testing)",
    )
    args = p.parse_args(argv)

    root = Path(args.root) if args.root else None
    globs = args.globs if args.globs else DEFAULT_GLOBS
    bad = findings(root=root, globs=globs)
    for source, lineno, token, ctx in bad:
        print(f"{source}:{lineno}: banned token {token!r} in operator-facing text — …{ctx}…")
    if bad:
        print(f"\n{len(bad)} banned token(s). Rewrite in the operator's own words.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
