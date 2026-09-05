from __future__ import annotations

import re
import tempfile
from datetime import date
from pathlib import Path

from .model import READER, RECORD

# --------------------------------------------------------------------------------------
# Derived vocabulary: the things this linter bans are read out of the code that owns them.
# --------------------------------------------------------------------------------------


def source_names() -> dict[str, str]:
    """`{source_id: human label}` straight from the collector, over an empty tree.

    The collector is the only place that knows what sources exist. Deriving the map here
    means a new source arrives already banned as an identifier AND already supplied with
    the name to use instead — the two halves of the fix land together.
    """
    from gtm_core.voc import collect as voc

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = voc.collect(root / "content", root / "profiles", "acme", today=date(2026, 1, 1))
    return {s["id"]: s["label"] for s in manifest["sources"]}


def expected_counts() -> dict[str, set[int]]:
    """What a truthful "N of these" claim in the brief may say.

    `speakers` accepts two values because both readings are honest: nine speaker labels
    exist, one of which (`mixed`) is an instruction to split rather than a speaker, so
    "eight" and "nine" are each defensible. Any other number is stale.
    """
    from gtm_core.voc import collect as voc
    from gtm_core.voc import watermark

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = voc.collect(root / "content", root / "profiles", "acme", today=date(2026, 1, 1))
    speakers = len(manifest["speakers"])
    return {
        "sources": {len(manifest["sources"])},
        "speakers": {speakers, speakers - 1},
        "lanes": {len(watermark.POLICIES)},
    }


_TAG_RE = re.compile(r"<[^>]+>")
_LINT_OK_RE = re.compile(r"<!--\s*lint-ok\s+(T\d)\s*:\s*(\S.*?)-->", re.IGNORECASE)
_BLOCKQUOTE_OPEN = re.compile(r"<blockquote\b", re.IGNORECASE)
_BLOCKQUOTE_CLOSE = re.compile(r"</blockquote>", re.IGNORECASE)


def _strip_tags(line: str) -> str:
    """Reader-visible text only. An `id="s4c"` attribute is not prose."""
    return _TAG_RE.sub(" ", line)


def _flatten(text: str) -> str:
    """Reader-visible prose as one line, with every character offset preserved.

    Line-based rules miss anything a hard wrap splits, and this repo wraps markdown at 100
    columns — `the five\\nspeakers` sailed through the stale-count check for exactly that
    reason, in the file that tells the skill how many speakers there are. Tags and newlines
    are replaced by *equal-length* runs of spaces rather than removed, so an offset in the
    flattened text still maps to the right line in the original.

    Style rules and HTML comments are blanked too. `/* speaker series */` in a stylesheet is
    code that no reader sees, and flagging it would push a writer toward worse variable
    names to satisfy a prose rule. `lint-ok` markers are read off the raw lines separately,
    so blanking comments here does not disarm the escape hatch.
    """

    def blank(match: re.Match[str]) -> str:
        return re.sub(r"\S", " ", match.group(0))

    hidden = re.sub(r"<style\b.*?</style>", blank, text, flags=re.DOTALL | re.IGNORECASE)
    hidden = re.sub(r"<script\b.*?</script>", blank, hidden, flags=re.DOTALL | re.IGNORECASE)
    hidden = re.sub(r"<!--.*?-->", blank, hidden, flags=re.DOTALL)
    blanked = _TAG_RE.sub(lambda m: " " * len(m.group(0)), hidden)
    # Markdown emphasis and code fences break word adjacency: `**all nine** external lanes`
    # is one phrase to a reader and three to a regex. Underscores are left alone — T1 needs
    # them to spot a snake_case id.
    for char in "*`":
        blanked = blanked.replace(char, " ")
    return blanked.replace("\n", " ").replace("\r", " ")


def _suppressions(lines: list[str]) -> dict[int, set[str]]:
    """`{line number: {tiers suppressed}}`, honouring the line above as well as the line itself."""
    out: dict[int, set[str]] = {}
    for idx, line in enumerate(lines, 1):
        for tier, _reason in _LINT_OK_RE.findall(line):
            for target in (idx, idx + 1):
                out.setdefault(target, set()).add(tier.upper())
    return out


def _quoted_lines(lines: list[str]) -> set[int]:
    """Line numbers inside a `<blockquote>`.

    Quoted material is somebody else's register. If a customer said "fetch", the brief
    must be able to print "fetch" — flagging it would teach people to paraphrase quotes,
    which is a worse outcome than the jargon.
    """
    inside = False
    out: set[int] = set()
    for idx, line in enumerate(lines, 1):
        opened = bool(_BLOCKQUOTE_OPEN.search(line))
        if inside or opened:
            out.add(idx)
        if opened:
            inside = True
        if _BLOCKQUOTE_CLOSE.search(line):
            inside = False
    return out


def _element_inner(markup: str, open_end: int, tag: str) -> str | None:
    """Inner HTML of the element whose opening tag ends at ``open_end``, or None if the
    document is too malformed to tell (in which case we say nothing rather than guess)."""
    depth, i = 1, open_end
    open_re = re.compile(rf"<{tag}[\s>]", re.I)
    close = f"</{tag}>"
    while i < len(markup):
        nxt_close = markup.find(close, i)
        if nxt_close == -1:
            return None
        nxt_open = open_re.search(markup, i, nxt_close)
        if nxt_open:
            depth += 1
            i = nxt_open.end()
            continue
        depth -= 1
        if depth == 0:
            return markup[open_end:nxt_close]
        i = nxt_close + len(close)
    return None


# --------------------------------------------------------------------------------------
# Plumbing
# --------------------------------------------------------------------------------------


def _blocks(text: str, tags: str = "p|li|td|h[1-6]") -> list[tuple[int, str]]:
    """`(offset, block)` for each closed prose container, so findings land near their text.

    The tag set differs by tier on purpose. T5 asks "can the reader follow this number?",
    and in a table the answer lives one cell over — so T5 takes the whole `<tr>`. T7 asks
    "is this block too dense?", where the cell is the right unit.
    """
    out: list[tuple[int, str]] = []
    for match in re.finditer(rf"<({tags})\b.*?</\1>", text, re.DOTALL | re.IGNORECASE):
        out.append((match.start(), match.group(0)))
    return out


def _is_mostly_quotation(block: str) -> bool:
    """True when over half the block sits inside quotation marks.

    Catches the evidence library, where a record is a filing's own sentence in curly quotes
    rather than a `<blockquote>`.
    """
    visible = _strip_tags(block)
    inside = sum(len(m.group(1)) for m in re.finditer(r"[“\"]([^”\"]{25,})[”\"]", visible))
    return bool(visible.strip()) and inside > len(visible.strip()) * 0.5


def _leaf_divs(text: str) -> list[tuple[int, str]]:
    """Innermost `<div>`s — where this brief actually keeps most of its prose.

    Without this, T7 measures only the `<p>` tags and reports a document of short
    paragraphs while the callout boxes run to two hundred words.
    """
    pattern = re.compile(r"<div\b[^>]*>((?:(?!<div\b)[\s\S])*?)</div>", re.IGNORECASE)
    return [(m.start(), m.group(0)) for m in pattern.finditer(text)]


def _line_index(text: str):
    """Offset → 1-based line number."""
    starts = [0]
    for idx, char in enumerate(text):
        if char == "\n":
            starts.append(idx + 1)

    def line_of(offset: int) -> int:
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    return line_of


def infer_surface(path: Path) -> str:
    """HTML is the reader surface; everything else is the internal record."""
    return READER if path.suffix.lower() in {".html", ".htm"} else RECORD
