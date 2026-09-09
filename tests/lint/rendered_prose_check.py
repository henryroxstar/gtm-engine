"""Refuse a bare number in prose that reaches a rendered page.

The failure this exists to stop, five times in one file's history: a figure computed once,
written into a sentence, and then outliving the data it described. "about five weeks" survived
the list it was measured on. "0 of 990 emails · 51 people · 3 sequences" was every number real
and every number a different campaign's. "the three each person is due" stayed put when the
campaign staged two touches. "17 re-angle, 7 drop" disagreed with the file it claimed to read,
and with itself three paragraphs later. "7 of the 18 recipients hold a seat the matrix has no
row for" was 15. Every one passed review, because a stale number reads exactly like a fresh one.

**The rule: a digit in rendered prose must be derived at render time, not typed.**

What is NOT caught here, deliberately, because it is not the failure mode:

* **Docstrings.** They are history and rationale — "on 2026-09-04 the tiles read 0 of 990" is a
  record of what happened, and freezing it is the point. A docstring cannot go stale about the
  past.
* **The stylesheet.** `padding:14px` is not a claim about anything.
* **Config constants** (``config.py``): a published benchmark and its citation year are facts
  about an external source, versioned with the source, not derived from this profile's data.
* **Identifiers that happen to contain digits** — `rule 9`, `touch 1`, `§2.1.2`, `1:1`, a year,
  a regex. Those name a thing; they do not count one.

Everything else needs an f-string interpolation, or an entry in ``rendered_prose_allow.txt``
with a dated reason — the same shape as the complexity ratchet, and for the same reason: the
escape hatch has to cost a sentence explaining itself.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
#: Packages whose string literals reach a rendered page.
SCANNED = ("gtm_core/email_campaign_dashboard",)
#: Facts about an external source, versioned with it — never derived from tenant data.
EXEMPT_FILES = {"config.py", "render.py"}
ALLOWLIST = Path(__file__).with_name("rendered_prose_allow.txt")

#: A digit that is COUNTING something. Excludes anything glued to a word, a colon, a dot, a
#: percent, a slash, a hyphen or a comma — which is what separates `12` from `1:1`, `§2.1.2`,
#: `13.5px`, `2026-09-04`, `\d{8}` and `90/day`.
_COUNT = re.compile(r"(?<![\w.:=#/,%(-])(\d{1,4})(?![\w.:%/,)-])")
#: Inline CSS: `margin:8px 0 0`, `padding:12px 14px`. A length is not a count, and the
#: stylesheet already lives in an exempt file — this catches the `style="..."` attributes that
#: are unavoidably inline in a template string.
_INLINE_CSS = re.compile(
    r"style\s*=\s*([\"\'])(?:(?!\1).)*\1|<pre[^>]*>|<div[^>]*>|<td[^>]*>|<p[^>]*>|<span[^>]*>"
)
#: Digit-bearing names that are labels, not counts.
_LABELS = re.compile(
    r"\b(?:rule|touch|step|day|slide|phase|tier|v|version|art(?:icle)?|§)\s*\d+"
    r"|\b(?:19|20)\d{2}\b|\bd\{\d+\}|\b\d+:\d+\b",
    re.IGNORECASE,
)


def _docstring_ids(tree: ast.AST) -> set[int]:
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = n.body[0] if getattr(n, "body", None) else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                out.add(id(first.value))
    return out


def load_allowlist() -> dict[str, str]:
    """``"file.py:42" -> reason``. A line without a ``#`` reason is itself a failure."""
    out: dict[str, str] = {}
    if not ALLOWLIST.is_file():
        return out
    for raw in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, reason = line.partition("#")
        out[key.strip()] = reason.strip()
    return out


def findings(root: Path | None = None) -> list[tuple[str, int, str, str]]:
    """``(relpath, lineno, the digit, the sentence around it)`` for each typed count."""
    root = root or ROOT
    out = []
    for pkg in SCANNED:
        for f in sorted((root / pkg).rglob("*.py")):
            if f.name in EXEMPT_FILES:
                continue
            tree = ast.parse(f.read_text(encoding="utf-8"))
            docs = _docstring_ids(tree)
            for n in ast.walk(tree):
                if not isinstance(n, ast.Constant) or not isinstance(n.value, str):
                    continue
                if id(n) in docs:
                    continue
                s = _LABELS.sub("", _INLINE_CSS.sub(" ", n.value))
                # Prose, not an argument: `"0"` passed to `str.rstrip` is a literal, not a
                # claim. Require something sentence-shaped around the digit.
                if " " not in s or len(re.findall(r"[A-Za-z]", s)) < 3:
                    continue
                for hit in _COUNT.finditer(s):
                    ctx = " ".join(s[max(0, hit.start() - 60) : hit.start() + 60].split())
                    out.append((str(f.relative_to(root)), n.lineno, hit.group(1), ctx))
    return out


def main(argv: list[str] | None = None) -> int:
    allow = load_allowlist()
    bad = []
    for rel, line, num, ctx in findings():
        if any(k in (f"{rel}:{line}", rel) for k in allow):
            continue
        bad.append((rel, line, num, ctx))
    for rel, line, num, ctx in bad:
        print(f"{rel}:{line}: typed count {num!r} in rendered prose — …{ctx}…")
    if bad:
        print(
            f"\n{len(bad)} typed count(s). Derive them at render time, or add a dated reason:\n"
            "  <file>:<line>  # YYYY-MM-DD why this number cannot go stale\n"
            "  (see this module's docstring for what is already exempt)"
        )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
