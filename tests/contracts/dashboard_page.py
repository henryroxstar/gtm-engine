"""Readers for the PS20 Phase 2 page tests — imported, never collected (no ``test_`` prefix).

A panel is the ``<section id="p-<tab>">`` a tab button shows. A section is the element carrying
``data-section`` (``format.section`` and the Operator notes groups). Tests read these, never a
page-wide substring (the Phase 1 rule).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

_VOID = frozenset({"area", "br", "col", "hr", "img", "input", "link", "meta", "source", "wbr"})


def panel(page: str, tab: str) -> str:
    """The inner HTML of one tab's panel."""
    at = page.index(f'<section id="p-{tab}"')
    start = page.index(">", at) + 1
    return page[start : page.index("</section>", start)]


def head(page: str) -> str:
    """Everything above the first panel: the title, the strip, the tab row."""
    return page.split('<section id="p-', 1)[0]


def block(html: str, marker: str) -> str:
    """The first element whose open tag contains ``marker``, open tag to its own close."""
    at = html.find(marker)
    if at == -1:
        return ""
    start = html.rindex("<", 0, at)
    tag = re.match(r"<(\w+)", html[start:]).group(1)
    depth = 0
    for mt in re.finditer(rf"<(/?){tag}\b[^>]*>", html[start:]):
        depth += -1 if mt.group(1) else 1
        if depth == 0:
            return html[start : start + mt.end()]
    return html[start:]


def section(html: str, sid: str) -> str:
    """The element carrying ``data-section="<sid>"``, or "" when the section is absent."""
    return block(html, f'data-section="{sid}"')


def classes(attrs: dict) -> set[str]:
    return set((attrs.get("class") or "").split())


class _Walk(HTMLParser):
    """Every element, with the panel it sits in and its open ancestors' attributes."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[tuple[str, dict]] = []
        self.found: list[tuple[str | None, str, dict, list[dict]]] = []

    def _panel(self) -> str | None:
        for tag, a in reversed(self.stack):
            if tag == "section" and a.get("id", "").startswith("p-"):
                return a["id"][2:]
        return None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        own = a["id"][2:] if tag == "section" and a.get("id", "").startswith("p-") else None
        self.found.append((own or self._panel(), tag, a, [x[1] for x in self.stack]))
        if tag not in _VOID:
            self.stack.append((tag, a))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return


def elements(html: str) -> list[tuple[str | None, str, dict, list[dict]]]:
    """``(panel, tag, attrs, ancestors' attrs)`` for every element, in document order."""
    w = _Walk()
    w.feed(html)
    return w.found


def section_ids(html: str) -> list[str]:
    return [a["data-section"] for _p, _t, a, _anc in elements(html) if "data-section" in a]


class _Text(HTMLParser):
    def __init__(self, skip: frozenset[str]) -> None:
        super().__init__()
        self.skip, self.stack, self.out = skip, [], []

    def handle_starttag(self, tag, attrs):
        if tag in _VOID:
            return
        a = {k: (v or "") for k, v in attrs}
        gone = "hidden" in a or tag in ("script", "style", "title") or bool(classes(a) & self.skip)
        self.stack.append(gone or (self.stack[-1] if self.stack else False))

    def handle_endtag(self, tag):
        if tag not in _VOID and self.stack:
            self.stack.pop()
        self.out.append(" ")

    def handle_data(self, data):
        if not (self.stack and self.stack[-1]):
            self.out.append(data)


def visible_text(html: str, *, skip: frozenset[str] = frozenset()) -> str:
    """What a reader sees: no ``hidden`` subtree, no script or style, and no element whose class
    is in ``skip`` (pass ``frozenset({"tech"})`` for the page with the toggle off)."""
    p = _Text(skip)
    p.feed(html)
    return " ".join("".join(p.out).split())
