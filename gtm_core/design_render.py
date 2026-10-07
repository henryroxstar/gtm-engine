"""Render a solution design's HTML companion from its markdown — the `.md` is the source.

Every design ships an ``.html`` next to its ``.md``. The page used to carry the markdown and turn
it into HTML in the reader's browser, with scripts fetched from a public CDN — so on a network
that blocks script hosts, or opened offline from an email, it came up blank. This CLI renders the
markdown here instead and writes the HTML into the page, between two markers::

    uv run python -m gtm_core.design_render <design.md> [--html <path>] [--check]

Hand-editing that HTML is how the two files drift: the reader sees one document and the next
revision starts from the other. So the direction is one-way — edit the ``.md``, then re-render.
Only the text between the markers changes; every other byte of the HTML is left as it was, so a
page created once from the skill's template keeps its styling across re-renders. Relative
``.svg`` / ``.png`` images are inlined as ``data:`` URIs so the page is one self-contained file —
whether written as markdown (``![alt](path)``) or as a raw ``<img src="path">``, which is how the
skill's plate and screenshot figures are written; each is confined to the ``.md``'s own directory
(:mod:`gtm_core.confine`) and capped in size, because a path in a document is author input and
the bytes it names end up in a file that is sent to a customer. Remote and existing ``data:``
images are left as written.

The page no longer sanitizes anything itself, and the markdown can carry text lifted from
untrusted research (§R5), so the rendered HTML passes an allowlist before it is written: tags,
attributes and URL schemes outside it are dropped and named on stderr, and ``<script>``,
``<style>``, inline ``<svg>`` and their kin go with their content. Comments are dropped as well,
which is also what keeps the end marker from ever appearing inside the content it closes.

Exit codes: 0 written (or, with ``--check``, already current); 1 ``--check`` found drift;
2 refused — no or several marker pairs (a page made from the earlier, CDN-loading template has
none), or an image outside the directory or over the cap.

No network, no subprocess (§R6); markdown-it-py is the one dependency outside the stdlib.
"""

from __future__ import annotations

import argparse
import base64
import html
import re
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

from markdown_it import MarkdownIt

from .confine import ConfinementError, confined_source_file
from .fsio import atomic_write_text

MAX_IMAGE_BYTES = 5 * 1024 * 1024
START = "<!-- design_render:start -->"
END = "<!-- design_render:end -->"
# What a page made from the earlier template carries in place of the markers.
_LEGACY = '<script type="text/markdown" id="src">'
_MIME = {".svg": "image/svg+xml", ".png": "image/png"}
# `![alt](target)` or `![alt](target "title")`; group 1 is the target.
_IMAGE = re.compile(r"!\[(?:[^\]\\]|\\.)*\]\(\s*([^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_FENCE = ("```", "~~~")

# markdown-it refuses every `data:` link but four raster image types, so the
# `data:image/svg+xml` URIs `_data_uri` writes would print as literal text. Its rule, plus SVG.
_BAD_LINK = re.compile(r"^(vbscript|javascript|file|data):")
_GOOD_DATA = re.compile(r"^data:image/(gif|png|jpeg|webp|svg\+xml);")

# The HTML the page keeps. A tag outside _TAGS is dropped and its text kept; one in _DROP_WHOLE
# goes with its content — code, styles, a nested document, or SVG/MathML markup.
_TAGS = frozenset(
    "a abbr b blockquote br caption code col colgroup dd del details div dl dt em figcaption "
    "figure h1 h2 h3 h4 h5 h6 hr i img ins kbd li mark ol p pre q s samp section small span "
    "strong sub summary sup table tbody td tfoot th thead time tr u ul wbr".split()
)
_VOID = frozenset({"br", "col", "hr", "img", "wbr"})
_DROP_WHOLE = frozenset(
    "audio head iframe math noembed noframes noscript object script select style svg template "
    "textarea title video xmp".split()
)
# A start tag that ends an open item of its own kind, and the elements such an item can be opened
# through: the HTML parser walks past <div>, <p> and inline elements, and stops at anything else.
_ITEMS = {"li": frozenset({"li"}), "dd": frozenset({"dd", "dt"}), "dt": frozenset({"dd", "dt"})}
_PASS_THROUGH = frozenset(
    "a abbr b code del div em i ins kbd mark p q s samp small span strong sub sup time u".split()
)
_ATTRS = frozenset({"class", "dir", "id", "lang", "role", "style", "title"})
_TAG_ATTRS = {
    "a": {"href"},
    "col": {"span"},
    "colgroup": {"span"},
    "details": {"open"},
    "img": {"src", "alt", "width", "height"},
    "ol": {"start", "reversed"},
    "td": {"colspan", "rowspan", "align"},
    "th": {"colspan", "rowspan", "align", "scope"},
    "time": {"datetime"},
}
_PREFIXED = re.compile(r"(?:aria|data)-[a-z0-9_.-]+")
_URL_SCHEMES = frozenset({"http", "https", "mailto", "tel"})
_DATA_IMAGE = re.compile(r"data:image/(?:gif|png|jpeg|webp|svg\+xml)[;,]", re.IGNORECASE)
_C0_SPACE = "".join(map(chr, range(0x21)))


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


def _inlinable(target: str) -> bool:
    """Whether ``target`` is a relative ``.svg``/``.png`` — the one rule for both image syntaxes."""
    return not _SCHEME.match(target) and Path(target).suffix.lower() in _MIME


def _inline_line(line: str, base: Path) -> tuple[str, int]:
    count = 0

    def swap(m: re.Match[str]) -> str:
        nonlocal count
        target = m.group(1)
        if not _inlinable(target):
            return m.group(0)  # http(s):, data: and other files stay as written
        count += 1
        start, end = m.start(1) - m.start(0), m.end(1) - m.start(0)
        return m.group(0)[:start] + _data_uri(target, base) + m.group(0)[end:]

    return _IMAGE.sub(swap, line), count


def _inlined_markdown(md_path: Path) -> tuple[str, int]:
    """The ``.md``'s text with relative images inlined, and how many were.

    Images inside a fenced code block are code samples and are left alone.
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
    return "\n".join(out), inlined


def _link_ok(url: str) -> bool:
    url = url.strip().lower()
    return not _BAD_LINK.search(url) or bool(_GOOD_DATA.search(url))


_MARKDOWN = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])
_MARKDOWN.validateLink = _link_ok


def _url_ok(value: str, *, image: bool) -> bool:
    """Whether following ``value`` can only reach a page, an address or an image — never code.

    Controls and spaces are trimmed from the ends, as a browser trims them, so `` javascript:``
    is read as the scheme it is. A control character left inside refuses the URL outright: a
    browser deletes tabs and newlines, so ``java&#9;script:`` would otherwise pass as relative. A
    relative URL passes unless it starts with two slashes: on a page opened from disk, that is a
    ``file:`` URL on another host.
    """
    url = value.strip(_C0_SPACE)
    if re.search(r"[\x00-\x1f\x7f]", url) or re.match(r"[/\\]{2}", url):
        return False
    scheme = _SCHEME.match(url)
    if scheme is None:
        return True
    if scheme.group(0)[:-1].lower() in _URL_SCHEMES:
        return True
    return image and bool(_DATA_IMAGE.match(url))


class _Allowlist(HTMLParser):
    """Re-serializes HTML from its parsed tokens, keeping only what the allowlist names.

    No markup passes through as written: text and attribute values are re-escaped, so input a
    browser would parse differently from this parser can change only what is kept — it cannot
    carry a tag or an attribute past the list. Every element it opens it also closes, an end tag
    with nothing of its own to close is dropped, and an item a browser would close implicitly
    (``<li>``, ``<dd>``, ``<dt>``) is closed in the output, so the content does not close the
    page's markup around it. That holds for the nesting the parser tracks; it does not model every
    implied close, and nothing it leaves out can run script.

    Given a ``base`` directory, an ``<img>`` whose ``src`` is a relative ``.svg``/``.png`` is
    inlined from it — the markdown pass cannot see a raw ``<img>``. Without one, nothing is read.
    """

    def __init__(self, base: Path | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.dropped: Counter[str] = Counter()
        self.inlined = 0
        self._base = base
        self._open: list[str] = []  # kept elements not yet closed, innermost last
        self._skip: list[str] = []  # the _DROP_WHOLE elements being skipped, innermost last

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROP_WHOLE:
            if not self._skip:
                self.dropped[f"<{tag}>"] += 1
            self._skip.append(tag)
        elif self._skip:
            return
        elif tag not in _TAGS:
            self.dropped[f"<{tag}>"] += 1
        else:
            if tag in _ITEMS:
                self._close_open_item(tag)
            self.out.append(f"<{tag}{self._attrs(tag, attrs)}>")
            if tag not in _VOID:
                self._open.append(tag)

    def _close_open_item(self, tag: str) -> None:
        """Close, in the output, the item a browser would close when ``tag`` starts.

        A new ``<li>`` ends an open ``<li>`` even through a ``<div>`` or ``<p>`` inside it (``<dd>``
        and ``<dt>`` likewise), so the later ``</div>`` would otherwise land on an outer element.
        """
        group = _ITEMS[tag]
        for depth in range(len(self._open) - 1, -1, -1):
            if self._open[depth] in group:
                while len(self._open) > depth:
                    self.out.append(f"</{self._open.pop()}>")
                return
            if self._open[depth] not in _PASS_THROUGH:
                return

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # `<br/>` is void anyway; a browser would leave `<div/>` open — it is written closed here.
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._skip:
            if tag in self._skip:
                del self._skip[len(self._skip) - 1 - self._skip[::-1].index(tag) :]
            return
        if tag not in self._open:
            return
        while (top := self._open.pop()) != tag:
            self.out.append(f"</{top}>")
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.out.append(html.escape(data, quote=False))

    def close(self) -> None:
        super().close()
        if self._skip:
            raise RenderError(
                f"<{self._skip[0]}> is opened and never closed, so everything after it would be "
                "left out of the page — put it in backticks if it is meant as text, or close it"
            )
        self.out.extend(f"</{tag}>" for tag in reversed(self._open))
        self._open.clear()

    def _attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        allowed = _TAG_ATTRS.get(tag, set())
        kept: list[str] = []
        for name, value in attrs:
            if not (name in _ATTRS or name in allowed or _PREFIXED.fullmatch(name)):
                self.dropped[f"{name}="] += 1
            elif name in ("href", "src") and not _url_ok(value or "", image=name == "src"):
                self.dropped[f"{name}= (unsafe URL)"] += 1
            else:
                if name == "src":
                    value = self._embedded(value)
                kept.append(f" {name}" if value is None else f' {name}="{html.escape(value)}"')
        return "".join(kept)

    def _embedded(self, src: str | None) -> str | None:
        """``src`` as a ``data:`` URI if it names a relative ``.svg``/``.png`` beside the design.

        Trimmed as :func:`_url_ok` trims it, so the path judged here is the one a browser would
        fetch. A refusal from :func:`_data_uri` is not caught: the render stops, as for markdown.
        """
        target = (src or "").strip(_C0_SPACE)
        if self._base is None or not _inlinable(target):
            return src
        self.inlined += 1
        return _data_uri(target, self._base)


def _render(markdown: str, base: Path | None) -> tuple[str, Counter[str], int]:
    parser = _Allowlist(base)
    parser.feed(_MARKDOWN.render(markdown))
    parser.close()
    return "".join(parser.out), parser.dropped, parser.inlined


def render_html(markdown: str, *, base: Path | None = None) -> tuple[str, Counter[str]]:
    """``markdown`` as the HTML the page shows, and a count of what the allowlist dropped.

    ``base`` is the directory a raw ``<img>``'s relative ``.svg``/``.png`` is inlined from; left
    out, no file is read and such an image keeps its path.
    """
    body, dropped, _ = _render(markdown, base)
    return body, dropped


def _region_span(page: str, html_path: Path) -> tuple[int, int]:
    """Start and end offsets of the rendered content between the page's one marker pair."""
    starts, ends = page.count(START), page.count(END)
    if starts == ends == 0 and _LEGACY in page:
        raise RenderError(
            f"{html_path} was made from the earlier template, which loads its renderer from a "
            "CDN and opens blank where that host is blocked — re-create it once from the current "
            "template (solution-design references/html-companion.md), then render again"
        )
    if starts != 1 or ends != 1:
        raise RenderError(f"expected exactly one {START} and one {END}, found {starts} and {ends}")
    start, end = page.index(START) + len(START), page.index(END)
    if end < start:
        raise RenderError(f"{END} comes before {START}")
    return start, end


def _first_difference(current: str, wanted: str) -> str:
    have, want = current.split("\n"), wanted.split("\n")
    for n, (a, b) in enumerate(zip(have, want, strict=False), start=1):
        if a != b:
            return (
                f"first difference at rendered line {n}: html has {a[:60]!r}, .md gives {b[:60]!r}"
            )
    n = min(len(have), len(want)) + 1
    longer = "html" if len(have) > len(want) else ".md"
    return f"first difference at rendered line {n}: the {longer} side has more lines"


def run(md_path: Path, html_path: Path, *, check: bool) -> int:
    if not html_path.is_file():
        raise RenderError(f"no HTML companion at {html_path} — create it once from the template")
    page = html_path.read_bytes().decode("utf-8")
    start, end = _region_span(page, html_path)
    markdown, inlined = _inlined_markdown(md_path)
    body, dropped, embedded = _render(markdown, md_path.resolve().parent)
    inlined += embedded
    region = "\n" + body
    if check:
        if page[start:end] == region:
            print(f"current: {html_path}")
            return 0
        print(f"stale: {html_path} — {_first_difference(page[start:end], region)}")
        return 1
    atomic_write_text(html_path, page[:start] + region + page[end:])
    print(f"wrote {html_path} from {md_path} ({inlined} image(s) inlined)")
    if dropped:
        found = ", ".join(f"{what} ×{n}" for what, n in sorted(dropped.items()))
        print(f"note: left out of the page (not on its allowlist): {found}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.design_render",
        description="Render a solution design's HTML companion from its markdown source.",
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
