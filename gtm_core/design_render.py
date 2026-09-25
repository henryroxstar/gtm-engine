"""Re-render a solution design's HTML companion from its markdown — the `.md` is the source.

Every design ships an ``.html`` next to its ``.md`` that embeds the markdown verbatim inside
``<script type="text/markdown" id="src">…</script>`` and renders it client-side. Hand-editing
that HTML is how the two drift: the reader sees one document and the next revision starts from
the other. This CLI makes the direction one-way — edit the ``.md``, then re-render::

    uv run python -m gtm_core.design_render <design.md> [--html <path>] [--check]

Only the script block's content changes; every other byte of the HTML is left as it was, so a
page created once from the skill's template keeps its styling across re-renders. Relative
``.svg`` / ``.png`` images are inlined as ``data:`` URIs so the page is one self-contained file;
each is confined to the ``.md``'s own directory (:mod:`gtm_core.confine`) and capped in size,
because a path in a document is author input and the bytes it names end up in a file that is
sent to a customer. Remote and existing ``data:`` images are left as written.

Exit codes: 0 written (or, with ``--check``, already current); 1 ``--check`` found drift;
2 refused — no or several script blocks, an image outside the directory or over the cap, or a
``</script`` in the text that would end the block early.

Stdlib only, no network, no subprocess (§R6).
"""

from __future__ import annotations

import argparse
import base64
import re
import sys
from pathlib import Path
from urllib.parse import unquote

from .confine import ConfinementError, confined_source_file
from .fsio import atomic_write_text

MAX_IMAGE_BYTES = 5 * 1024 * 1024
OPEN_TAG = '<script type="text/markdown" id="src">'
_CLOSE = re.compile(r"</script", re.IGNORECASE)
_MIME = {".svg": "image/svg+xml", ".png": "image/png"}
# `![alt](target)` or `![alt](target "title")`; group 1 is the target.
_IMAGE = re.compile(r"!\[(?:[^\]\\]|\\.)*\]\(\s*([^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_FENCE = ("```", "~~~")


class RenderError(Exception):
    """The companion cannot be rendered safely; the message says why."""


def _data_uri(target: str, base: Path) -> str:
    path = unquote(target)
    if path.startswith(("/", "\\")) or ".." in re.split(r"[/\\]", path):
        raise RenderError(f"image path must stay inside the design's folder: {target}")
    try:
        resolved = confined_source_file(
            base / path, content_root=base, max_bytes=MAX_IMAGE_BYTES, action="embed an image"
        )
    except ConfinementError as exc:
        raise RenderError(str(exc)) from exc
    encoded = base64.b64encode(resolved.read_bytes()).decode("ascii")
    return f"data:{_MIME[resolved.suffix.lower()]};base64,{encoded}"


def _inline_line(line: str, base: Path) -> tuple[str, int]:
    count = 0

    def swap(m: re.Match[str]) -> str:
        nonlocal count
        target = m.group(1)
        if _SCHEME.match(target) or Path(target).suffix.lower() not in _MIME:
            return m.group(0)  # http(s):, data: and other files stay as written
        count += 1
        start, end = m.start(1) - m.start(0), m.end(1) - m.start(0)
        return m.group(0)[:start] + _data_uri(target, base) + m.group(0)[end:]

    return _IMAGE.sub(swap, line), count


def embedded_text(md_path: Path) -> tuple[str, int]:
    """The script block's content for ``md_path``, and how many images were inlined.

    Shaped as the skill's template fills it — a newline, the markdown, a newline — so a page
    made from the template and never touched checks clean. Images inside a fenced code block
    are code samples and are left alone.
    """
    base = md_path.resolve().parent
    out: list[str] = []
    inlined = 0
    in_fence = False
    for line in md_path.read_bytes().decode("utf-8").split("\n"):
        if line.lstrip().startswith(_FENCE):
            in_fence = not in_fence
        elif not in_fence:
            line, n = _inline_line(line, base)
            inlined += n
        out.append(line)
    text = "\n" + "\n".join(out) + "\n"
    if _CLOSE.search(text):
        raise RenderError("the markdown contains '</script' — it would end the embedded block")
    return text, inlined


def _block_span(html: str) -> tuple[int, int]:
    """Start and end offsets of the one markdown script block's content."""
    count = html.count(OPEN_TAG)
    if count != 1:
        raise RenderError(f"expected exactly one {OPEN_TAG} block, found {count}")
    start = html.index(OPEN_TAG) + len(OPEN_TAG)
    close = _CLOSE.search(html, start)
    if close is None:
        raise RenderError(f"{OPEN_TAG} is never closed")
    return start, close.start()


def _first_difference(current: str, wanted: str) -> str:
    have, want = current.split("\n"), wanted.split("\n")
    for n, (a, b) in enumerate(zip(have, want, strict=False), start=1):
        if a != b:
            return f"first difference at block line {n}: html has {a[:60]!r}, .md gives {b[:60]!r}"
    n = min(len(have), len(want)) + 1
    longer = "html" if len(have) > len(want) else ".md"
    return f"first difference at block line {n}: the {longer} side has more lines"


def run(md_path: Path, html_path: Path, *, check: bool) -> int:
    if not html_path.is_file():
        raise RenderError(f"no HTML companion at {html_path} — create it once from the template")
    html = html_path.read_bytes().decode("utf-8")
    start, end = _block_span(html)
    text, inlined = embedded_text(md_path)
    if check:
        if html[start:end] == text:
            print(f"current: {html_path}")
            return 0
        print(f"stale: {html_path} — {_first_difference(html[start:end], text)}")
        return 1
    atomic_write_text(html_path, html[:start] + text + html[end:])
    print(f"wrote {html_path} from {md_path} ({inlined} image(s) inlined)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.design_render",
        description="Re-render a solution design's HTML companion from its markdown source.",
    )
    parser.add_argument("md", type=Path, help="the design's .md — the single source")
    parser.add_argument("--html", type=Path, help="the companion (default: the .md path as .html)")
    parser.add_argument(
        "--check", action="store_true", help="write nothing; exit 1 if the HTML is stale"
    )
    args = parser.parse_args(argv)
    try:
        return run(args.md, args.html or args.md.with_suffix(".html"), check=args.check)
    except (RenderError, OSError, UnicodeDecodeError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
