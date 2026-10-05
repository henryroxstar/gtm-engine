"""R1.4: find the members a captured list names, and prove each one is in the page's own words.

``member_in_capture`` is deliberately not ``signal_sources.validate_source_evidence``: that one
has a 20-character floor and matches substrings. A member name is short and a substring of
another word is not the member, so this check is word-bounded and refuses names of three
characters or fewer.
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
import urllib.parse
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field, replace
from itertools import islice

from .. import minischema
from ..signal_sources import normalise_quote_whitespace, page_text

MIN_NAME_CHARS = 4
#: A page offering more distinct names than this is not a member list, and a cap applied *before*
#: any name is checked is what keeps the work linear in the page rather than in names x page.
MAX_CANDIDATES = 5000
_MAX_HEADING_BLOCK = 50
_MAX_OCCURRENCES = 200
_MAX_LINK_LINES = 10
_MAX_OPEN_LINKS = 16
_MAX_URL = 2000
_BRACKET = re.compile(r"\[|\]\(")
_MALFORMED = "malformed-link"

#: Every pattern below is bounded or linear, and the block stripper is a single forward scan: a
#: page is untrusted input of any size, and a pattern that rescans to the end of the page from
#: every ``<`` or ``[`` turns a few hundred kilobytes of brackets into minutes of work.
_TAGS = re.compile(r"<[^<>]{0,1000}>")
_JSON_U = re.compile(r"\\u([0-9a-fA-F]{4})")
_MD_IMAGE = re.compile(r"!\[[^\]\n]{0,300}\]\([^()\s]{0,2000}\)")
_MD_LINK_TEXT = re.compile(r"\[([^\]\n]{0,300})\]\([^()\s]{0,2000}(?:\s+\"[^\"\n]{0,300}\")?\)")
_MD_LINK_URL = re.compile(
    r"(?<!!)\[[^\]\n]{0,300}\]\(([^()\s]{1,2000})(?:\s+\"[^\"\n]{0,300}\")?\)"
)
_MD_MARKS = re.compile(r"\*\*|__|^[ \t]*(?:#{1,6}[ \t]+|>[ \t]?|[-*+][ \t]+)", re.MULTILINE)
# ASCII-only case folding: a browser does not read ``<ſcript>`` as a script tag.
_OPENERS = re.compile(r"<!--|<(?:script|style)(?=[\s/>]|$)", re.IGNORECASE | re.ASCII)
_CLOSERS = {
    "script": re.compile(r"</script", re.IGNORECASE | re.ASCII),
    "style": re.compile(r"</style", re.IGNORECASE | re.ASCII),
}
_TAG_NAME = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9]*)")
_HREF = re.compile(
    r"""\bhref\s*=\s*(?:"([^"]{0,2000})"|'([^']{0,2000})'|([^\s"'<>]{1,2000}))""",
    re.IGNORECASE | re.ASCII,
)
_BLOCK_TAGS = frozenset(
    {"li", "tr", "p", "br", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table", "dd", "dt"}
)
_HOST_TOKEN = re.compile(r"(?<![A-Za-z0-9.-])[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?![A-Za-z0-9-])")
_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*):")
_HEADING_LINE = re.compile(r" {0,3}#{1,6}(?:[ \t]|$)")
_LIST_ITEM = re.compile(r"\s*(?:[-*+]|\d{1,3}[.)])[ \t]")
_WORD = re.compile(r"\w")
_RUN = re.compile(r"\w+")


def _strip_blocks(text: str) -> str:
    """Drop ``<script>``/``<style>`` blocks and comments; an unclosed one runs to the end."""
    out: list[str] = []
    pos = 0
    while True:
        m = _OPENERS.search(text, pos)
        if m is None:
            out.append(text[pos:])
            return " ".join(out)
        out.append(text[pos : m.start()])
        if m.group() == "<!--":
            end = text.find("-->", m.end())
            if end < 0:
                return " ".join(out)
            pos = end + 3
            continue
        close = _CLOSERS[m.group()[1:].lower()].search(text, m.end())
        if close is None:
            return " ".join(out)
        gt = text.find(">", close.end())
        pos = len(text) if gt < 0 else gt + 1


def _tags_to_marks(text: str) -> str:
    """Tags out: an anchor becomes a markdown link, a block tag a line break, the rest a space."""
    hrefs: list[str] = []

    def repl(m: re.Match[str]) -> str:
        tag = _TAG_NAME.match(m.group())
        if tag is None:
            return " "
        closing, name = tag.group(1), tag.group(2).lower()
        if name == "a":
            if closing:
                href = hrefs.pop() if hrefs else ""
                return f"]({href})" if href else ""
            found = _HREF.search(m.group())
            href = (
                html.unescape(next((g for g in found.groups() if g is not None), "")).strip()
                if found
                else ""
            )
            hrefs.append(urllib.parse.quote(href, safe="/:?#@!$&'*+,;=%-._~"))
            return "[" if href else ""
        return "\n" if name in _BLOCK_TAGS else " "

    return _TAGS.sub(repl, text)


def _visible_line(line: str) -> str:
    """One line's words: images dropped, a link kept as its text, list/heading marks removed."""
    return _MD_MARKS.sub("", _MD_LINK_TEXT.sub(r"\1", _MD_IMAGE.sub(" ", line)))


def visible_text(raw: object) -> str:
    """The words a reader would see: page text, minus scripts, styles, tags and link targets."""
    return "\n".join(_visible_line(ln) for ln in Page(page_text(raw)).lines)


def normalise(text: str) -> str:
    """Entity-decoded, JSON-unescaped, NFKC, quote-folded, casefolded, whitespace-collapsed."""
    t = html.unescape(text)
    t = _JSON_U.sub(lambda m: chr(int(m.group(1), 16)), t)
    t = unicodedata.normalize("NFKC", t)
    return normalise_quote_whitespace(t).casefold()


def name_too_short(name: str) -> bool:
    return len(normalise(name)) < MIN_NAME_CHARS


def _host(url: str) -> str | None:
    """The URL's host; ``""`` when it has none, ``None`` when its authority is malformed."""
    try:
        host = urllib.parse.urlsplit(url.strip()).hostname or ""
    except ValueError:
        return None
    return host.lower().removeprefix("www.")


def _link_host(url: str) -> str:
    """A host a link or a brain-supplied domain names: http(s) or scheme-less, else ``""``."""
    u = url.strip()
    scheme = _SCHEME.match(u)
    if scheme and scheme.group(1).lower() not in ("http", "https"):
        return ""
    return _host(u if scheme or u.startswith("//") else "//" + u) or ""


class Page:
    """One capture, prepared once: its words (to verify names) and its entries (to verify domains).

    An *entry* is what one member would be: a line, plus the indented lines under a list item,
    plus (for a heading) the lines up to the next heading. Every question asked of a page after
    construction is answered from an index, never by rescanning the page.
    """

    def __init__(self, text: str) -> None:
        self.stripped = _strip_blocks(text)
        self.lines = _tags_to_marks(self.stripped).split("\n")
        parts: list[str] = []
        self._starts: list[int] = []
        self._owners: list[int] = []
        pos = 0
        for i, line in enumerate(self.lines):
            norm = normalise(_visible_line(line))
            if norm:
                parts.append(norm)
                self._starts.append(pos)
                self._owners.append(i)
                pos += len(norm) + 1
        self.text = " ".join(parts)
        self._runs: dict[str, list[int]] | None = None
        self._line_hosts: dict[int, frozenset[str]] = {}
        self._link_hosts = self._find_link_hosts()

    def _find_link_hosts(self) -> dict[int, frozenset[str]]:
        """``{line: hosts}`` for the lines a multi-line link runs over, before the one it closes on.

        A logo link is written ``[![logo](img)`` / ``**Name**`` / ``(opens in new tab)](url)``: the
        name is inside the link and the address closes it a few lines later. The address belongs
        to the lines inside that one link and to nothing else, so two links chained on a line
        never lend each other an address. One left-to-right pass; the look-ahead is bounded, so a
        page of unclosed brackets costs a little per bracket, not the whole page.
        """
        text = "\n".join(self.lines)
        starts = [0]
        for line in self.lines[:-1]:
            starts.append(starts[-1] + len(line) + 1)
        closers = [m.start() for m in re.finditer(r"\)", text)]
        out: dict[int, set[str]] = {}
        opened: list[int] = []
        for m in _BRACKET.finditer(text):
            line = bisect_right(starts, m.start()) - 1
            if m.group() == "[":
                opened.append(line)
                del opened[:-_MAX_OPEN_LINKS]
            elif opened:
                first = opened.pop()
                k = bisect_left(closers, m.end())
                if k == len(closers) or closers[k] - m.end() > _MAX_URL:
                    continue
                last = bisect_right(starts, closers[k]) - 1
                target = text[m.end() : closers[k]].split(None, 1)
                host = _link_host(target[0]) if target else ""
                if host and 0 < last - first <= _MAX_LINK_LINES:
                    for i in range(first, last):
                        out.setdefault(i, set()).add(host)
        return {i: frozenset(h) for i, h in out.items()}

    def _index(self) -> dict[str, list[int]]:
        if self._runs is None:
            runs: dict[str, list[int]] = {}
            for m in _RUN.finditer(self.text):
                runs.setdefault(m.group(), []).append(m.start())
            self._runs = runs
        return self._runs

    def _bounded(self, wanted: str, start: int) -> bool:
        end = start + len(wanted)
        return (
            start >= 0
            and self.text.startswith(wanted, start)
            and not (start and _WORD.match(self.text, start - 1))
            and not _WORD.match(self.text, end)
        )

    def occurrences(self, wanted: str):
        """Each ``(start, end)`` where the normalised ``wanted`` stands as a whole word or phrase."""
        runs = _RUN.findall(wanted)
        if not runs:  # punctuation only: nothing to index by
            at = self.text.find(wanted)
            while at >= 0:
                if self._bounded(wanted, at):
                    yield at, at + len(wanted)
                at = self.text.find(wanted, at + 1)
            return
        index = self._index()
        rarest = min(set(runs), key=lambda r: len(index.get(r, ())))
        offset = next(m.start() for m in _RUN.finditer(wanted) if m.group() == rarest)
        for at in index.get(rarest, ()):
            if self._bounded(wanted, at - offset):
                yield at - offset, at - offset + len(wanted)

    def _extent(self, i: int) -> range:
        lines = self.lines
        j = i + 1
        if _HEADING_LINE.match(lines[i]):
            while (
                j < len(lines) and j - i <= _MAX_HEADING_BLOCK and not _HEADING_LINE.match(lines[j])
            ):
                j += 1
        elif _LIST_ITEM.match(lines[i]):
            while j < len(lines) and lines[j][:1] in (" ", "\t") and not _LIST_ITEM.match(lines[j]):
                j += 1
        return range(i, j)

    def _hosts_of(self, i: int) -> frozenset[str]:
        if i not in self._line_hosts:
            hosts: set[str] = set()
            for k in self._extent(i):
                line = self.lines[k]
                hosts.update(
                    h for m in _MD_LINK_URL.finditer(line) if (h := _link_host(m.group(1)))
                )
                hosts.update(
                    t.group().lower().removeprefix("www.")
                    for t in _HOST_TOKEN.finditer(_visible_line(line))
                )
            for k in self._extent(i):
                hosts.update(self._link_hosts.get(k, ()))
            self._line_hosts[i] = frozenset(hosts)
        return self._line_hosts[i]

    def hosts_near(self, span: tuple[int, int]) -> frozenset[str]:
        """Hosts carried by the entry (or entries) a match in :attr:`text` lies in."""
        first = bisect_right(self._starts, span[0]) - 1
        last = bisect_right(self._starts, max(span[0], span[1] - 1)) - 1
        return frozenset().union(*(self._hosts_of(self._owners[k]) for k in range(first, last + 1)))


def haystack(raw: object) -> Page:
    """The prepared visible words of a capture: built once, then searched for every name."""
    return Page(page_text(raw))


def member_in_capture(name: str, text: str, *, prepared: Page | None = None) -> bool:
    """True when ``name`` appears in ``text``'s visible words as a whole word or phrase.

    ``prepared`` is ``haystack(text)`` when the caller checks many names against one page.
    """
    wanted = normalise(name)
    if len(wanted) < MIN_NAME_CHARS:
        return False
    page = prepared if prepared is not None else haystack(text)
    return next(page.occurrences(wanted), None) is not None


@dataclass(frozen=True)
class Member:
    name: str
    #: A domain the page itself carries for the member (an outbound link), or ``""``.
    domain: str = ""
    #: Why the member has no domain when the page was no help, for the queue.
    reason: str = ""


@dataclass
class Extraction:
    members: list[Member] = field(default_factory=list)
    refused_short: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    #: Names whose domain was not on the page, or was one domain shared by several names.
    domain_dropped: list[str] = field(default_factory=list)


_LINK = re.compile(r"\[([^\]\n]{1,300})\]\(([^()\s]{1,2000})(?:\s+\"[^\"\n]{0,300}\")?\)")
_HEADING = re.compile(r"^(#{1,6})[ \t]+([^\n]*)$", re.MULTILINE)
_MAX_HEADING_TITLE = 500


def _outbound_domain(url: str, source_url: str) -> str | None:
    """The link's host when it leaves the source site, ``""`` for an in-site or hostless link,
    ``None`` when its authority is malformed."""
    host = _host(url)
    if not host:
        return host
    return "" if host == _host(source_url) else host


def _link_member(name: str, url: str, source_url: str) -> Member:
    domain = _outbound_domain(url, source_url)
    if domain is None:  # one broken link must not abort the source: this candidate is skipped
        return Member(name.strip(), "", _MALFORMED)
    return Member(name.strip(), domain)


def _links(text: str, args: dict, source_url: str):
    need = str(args.get("href_contains", ""))
    for m in _LINK.finditer(text):
        if not need or need in m.group(2):
            yield _link_member(m.group(1), m.group(2), source_url)


def _table_column(text: str, args: dict, source_url: str):
    want = str(args.get("column", "")).strip().casefold()
    if not want:
        raise ValueError("table_column needs a 'column' argument naming the header to read")
    return _table_rows(text, want, source_url)


def _table_rows(text: str, want: str, source_url: str):
    col = -1
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            col = -1
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if set("".join(cells)) <= set("-: "):
            continue
        if col < 0:
            col = next((i for i, c in enumerate(cells) if c.casefold() == want), -2)
            continue
        if 0 <= col < len(cells) and cells[col]:
            link = _LINK.search(cells[col])
            yield (
                _link_member(link.group(1), link.group(2), source_url)
                if link
                else Member(cells[col])
            )


def _heading_list(text: str, args: dict, source_url: str):
    level = int(args.get("level", 2))
    for m in _HEADING.finditer(text):
        title = m.group(2).rstrip(" \t#")
        if len(m.group(1)) == level and 0 < len(title) <= _MAX_HEADING_TITLE:
            yield Member(_LINK.sub(r"\1", title).strip())


_DETERMINISTIC = {"links": _links, "table_column": _table_column, "heading_list": _heading_list}


def extract_members(
    extractor: str,
    args: dict,
    raw: str,
    source_url: str,
    *,
    proposed: list[Member] | None = None,
) -> Extraction:
    """The verified members of one capture, in page order, each name once.

    ``brain_list`` has no deterministic rule: the brain proposes names (``proposed``) and this
    code keeps only those the page itself says. Every candidate of every extractor passes
    ``member_in_capture``, so a name that only a parser or a model produced never survives.
    More than :data:`MAX_CANDIDATES` distinct names is refused (``ValueError``) before any is
    checked, and a malformed ``column`` argument is refused the same way.
    """
    page = haystack(raw)
    candidates = (
        proposed
        if extractor == "brain_list"
        else _DETERMINISTIC[extractor](page.stripped, args, source_url)
    )
    out = Extraction()
    seen: set[str] = set()
    broken: set[str] = set()
    for cand in candidates or []:
        key = normalise(cand.name)
        if not key or key in seen or key in broken:
            continue
        if len(seen) + len(broken) >= MAX_CANDIDATES:
            raise ValueError(f"the page offers more than {MAX_CANDIDATES} candidate names")
        if cand.reason == _MALFORMED:
            broken.add(key)
            out.unverified.append(cand.name)
            continue
        seen.add(key)
        if name_too_short(cand.name):
            out.refused_short.append(cand.name)
        elif not member_in_capture(cand.name, raw, prepared=page):
            out.unverified.append(cand.name)
        else:
            out.members.append(_domain_on_page(cand, extractor, page, out))
    return _drop_shared_domains(out)


def _domain_on_page(cand: Member, extractor: str, page: Page, out: Extraction) -> Member:
    """A model-proposed domain counts only if the member's own entry on the page carries it.

    "Carries" is a link target or a whole hostname in that entry's text: not a substring of a
    longer host or address, not inside a comment, script or style block, and not another
    member's entry.
    """
    if extractor != "brain_list" or not cand.domain:
        return cand
    wanted = _link_host(cand.domain)
    spans = islice(page.occurrences(normalise(cand.name)), _MAX_OCCURRENCES)
    if wanted and any(wanted in page.hosts_near(span) for span in spans):
        return cand
    out.domain_dropped.append(cand.name)
    return replace(cand, domain="", reason="domain-not-on-page")


def _drop_shared_domains(out: Extraction) -> Extraction:
    """One domain linked from several different names is a platform, not any one member's own."""
    names_by_domain: dict[str, list[str]] = {}
    for m in out.members:
        if m.domain:
            names_by_domain.setdefault(m.domain.strip().lower().removeprefix("www."), []).append(
                m.name
            )
    shared = {d for d, names in names_by_domain.items() if len(names) > 1}
    if shared:
        out.members = [
            replace(m, domain="", reason="shared-domain")
            if m.domain.strip().lower().removeprefix("www.") in shared
            else m
            for m in out.members
        ]
        out.domain_dropped += [n for d in shared for n in names_by_domain[d]]
    return out


BRAIN_LIST_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["name"],
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "domain": {"type": "string"},
        },
    },
}


def parse_brain_list(text: str) -> list[Member]:
    """The names a brain proposed for a ``brain_list`` source, validated before anything uses them.

    Structured model output is untrusted: a wrong shape is refused here, and each name that
    passes is still checked against the page by :func:`extract_members`.
    """
    try:
        data = json.loads(text)
    except ValueError:
        raise ValueError("brain list is not valid JSON") from None
    errors = minischema.validate(data, BRAIN_LIST_SCHEMA)
    if errors:
        raise ValueError(f"brain list does not match its schema: {errors[0]}")
    return [Member(str(item["name"]).strip(), str(item.get("domain", "")).strip()) for item in data]
