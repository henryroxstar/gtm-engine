from __future__ import annotations

import re

from .model import ERROR, WARN, Finding
from .parse import (
    _blocks,
    _element_inner,
    _flatten,
    _is_mostly_quotation,
    _leaf_divs,
    _line_index,
    _quoted_lines,
    _strip_tags,
)


def t10_render_integrity(text: str, line_of) -> list[Finding]:
    """Does the markup actually render as written?

    The `.html` companion is hand-authored against its own inline stylesheet, and nothing
    else checks that the two agree. Twice now a brief has shipped with markup naming classes
    the sheet never defined — a whole section rendered as unstyled run-on text, and every
    number in a table collided with its own sub-label ("<b>8</b><span class=sub>8 companies"
    read as "88 companies", which a reader reasonably parsed as eighty-eight). Both were
    caught by a human reading the published page, which is the wrong last line of defence.

    Two rules, both statically decidable from the file alone — no browser, no network:

    * ``class-not-styled`` — a class appears in ``class="..."`` and in no CSS rule. This is
      the defect that produced the unstyled section: a container class that simply did not
      exist, so the block inherited body text.
    * ``glued-inline`` — a classed inline element abuts the preceding text with no
      whitespace, and its class declares a *vertical* margin but is never given a block
      display. Vertical margins do not apply to inline boxes, so the author's intended
      spacing is silently dropped and the two texts run together. This is the "88" bug
      exactly, and it is invisible in the source: the markup looks correctly separated.
    """
    findings: list[Finding] = []
    styles = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", text, re.S | re.I))
    if not styles:
        return findings
    markup = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.S | re.I)

    defined = set(re.findall(r"\.(-?[A-Za-z_][\w-]*)", styles))
    used: dict[str, int] = {}
    for m in re.finditer(r'class="([^"]*)"', markup):
        for cls in m.group(1).split():
            used.setdefault(cls, m.start())
    for cls in sorted(set(used) - defined):
        findings.append(
            Finding(
                "T10",
                "class-not-styled",
                ERROR,
                line_of(used[cls]),
                f'class="{cls}"',
                f'no CSS rule defines ".{cls}" — it renders unstyled; '
                f"add the rule or use an existing class",
            )
        )

    # Rules mentioning a class, so we can ask what that class actually declares.
    rules = re.findall(r"([^{}]+)\{([^}]*)\}", styles)
    for cls in sorted(set(used) & defined):
        body = " ".join(b for sel, b in rules if re.search(rf"\.{re.escape(cls)}(?![\w-])", sel))
        if not body:
            continue
        vertical = re.search(r"margin(-top|-bottom)?\s*:", body)
        blockish = re.search(r"display\s*:\s*(block|grid|flex|table|list-item|inline-block)", body)
        if not vertical or blockish:
            continue
        for m in re.finditer(
            rf'(\S)(<(?:span|b|i|em)[^>]*class="[^"]*\b{re.escape(cls)}\b)', markup
        ):
            findings.append(
                Finding(
                    "T10",
                    "glued-inline",
                    ERROR,
                    line_of(m.start()),
                    _flatten(markup[max(0, m.start() - 26) : m.start() + 34]),
                    f'".{cls}" sets a vertical margin but stays inline, so the margin is '
                    f"dropped and this runs into the text before it — give it "
                    f"display:block (scope it, e.g. `td .{cls}`) or add a separator",
                )
            )
    return findings


def t13_component_contract(text: str, line_of) -> list[Finding]:
    """Is a styled component being used with markup its own CSS cannot style?

    T10 catches a class with *no* rule. This catches the subtler and more damaging case: the
    class exists, the rule exists, and the markup still renders wrong because the rule
    targets a **child that was never written**.

    The 2026-08-18 brief shipped a bar chart with invisible bars. The stylesheet says::

        .dseg   { display:flex; height:9px; overflow:hidden }   /* the track  */
        .dseg i { display:block; height:100% }                  /* the fill   */

    — the coloured fill is an inner ``<i>``. The author wrote ``<span class="dseg"
    style="width:52%"></span>``: a track with no fill. Every class was defined, every class
    was spelled right, T10 passed, and the chart rendered as five empty outlines.

    Two rules, both derived from the stylesheet rather than transcribed, and both chosen to
    fire only where the absence actually costs the reader something:

    * ``styled-child-missing`` — the child's rule **paints or sizes** it (``display``,
      ``width``, ``height``, ``background``, ``border``) and no such child exists. When a
      rule that draws something has nothing to draw on, the component renders empty. A
      child rule that only sets typography is deliberately NOT flagged: a ``.note`` whose
      stylesheet also styles ``.note li`` is simply a callout that happens not to contain a
      list this time, which is correct and common. That distinction was measured — the
      typography-inclusive version of this check produced 14 false positives on one prior
      brief and 1 true one.
    * ``list-container-not-a-list`` — a class that declares itself a list container (its own
      rule sets ``list-style``, or the sheet styles ``.cls dt`` / ``.cls dd``) is applied to
      an element that carries no list item. This is the glossary defect: ``.gloss`` is styled
      through ``.gloss dt`` / ``.gloss dd`` and was written as ``<div><b>…</b><span>…</span></div>``,
      so the definition typography silently never applied.
    """
    # (?<![-\w]) not \b: "\bheight" also matches inside "line-height", which made every
    # typographic rule look like a painting one and reintroduced the false positives.
    PAINTS = re.compile(r"(?<![-\w])(display|width|height|background|border|box-shadow)\s*:")
    findings: list[Finding] = []
    styles = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", text, re.S | re.I))
    if not styles:
        return findings
    markup = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.S | re.I)
    rules = re.findall(r"([^{}]+)\{([^}]*)\}", styles)

    painted: dict[str, set[str]] = {}  # cls -> child tags whose rule draws something
    listish: dict[str, set[str]] = {}  # cls -> the list-item tags it expects
    own_body: dict[str, str] = {}
    for selector, body in rules:
        for sel in selector.split(","):
            sel = sel.strip()
            m = re.fullmatch(r"\.(-?[A-Za-z_][\w-]*)", sel)
            if m:
                own_body[m.group(1)] = own_body.get(m.group(1), "") + " " + body
                continue
            m = re.fullmatch(r"\.(-?[A-Za-z_][\w-]*)\s+([a-z]+)", sel)
            if not m:
                continue
            cls, tag = m.group(1), m.group(2)
            if tag in {"dt", "dd"}:
                listish.setdefault(cls, set()).add(tag)
            elif tag == "li":
                listish.setdefault(cls, set())  # confirmed below by list-style
            if PAINTS.search(body):
                painted.setdefault(cls, set()).add(tag)
    # An `li` rule only implies a list container when the class calls itself one.
    for cls in list(listish):
        if not listish[cls] and "list-style" not in own_body.get(cls, ""):
            del listish[cls]
        elif not listish[cls]:
            listish[cls] = {"li"}

    def elements(cls: str):
        for m in re.finditer(rf'<(\w+)[^>]*class="[^"]*\b{re.escape(cls)}\b[^"]*"[^>]*>', markup):
            inner = _element_inner(markup, m.end(), m.group(1))
            if inner is not None:
                yield m, inner

    for cls, tags in sorted(painted.items()):
        for m, inner in elements(cls):
            missing = sorted(t for t in tags if not re.search(rf"<{t}[\s>]", inner))
            if not missing:
                continue
            findings.append(
                Finding(
                    "T13",
                    "styled-child-missing",
                    ERROR,
                    line_of(m.start()),
                    _flatten(markup[m.start() : m.start() + 70]),
                    f'".{cls}" draws through ".{cls} {missing[0]}" but this element contains '
                    f"no <{missing[0]}> — the rule that paints it has nothing to paint, so "
                    f"the component renders empty",
                )
            )

    for cls, tags in sorted(listish.items()):
        for m, inner in elements(cls):
            if any(re.search(rf"<{t}[\s>]", inner) for t in tags):
                continue
            want = "/".join(sorted(tags))
            findings.append(
                Finding(
                    "T13",
                    "list-container-not-a-list",
                    ERROR,
                    line_of(m.start()),
                    _flatten(markup[m.start() : m.start() + 70]),
                    f'".{cls}" is styled as a list container (".{cls} {want}") but this '
                    f"element has no <{want}> — use the real list markup "
                    f"(<dl>/<dt>/<dd> or <ul>/<li>) or a plain container for prose",
                )
            )
    return findings


def t7_density(text: str) -> list[Finding]:
    """Length heuristics. Advisory always — prose length is a judgement call."""
    findings: list[Finding] = []
    line_of = _line_index(text)
    seen: set[tuple[str, int, str]] = set()
    # `_blocks` yields leaf-level prose and is always measured. A leaf `<div>` is only prose
    # when it holds no block children of its own: a table is dense by design (that is what a
    # table is for), and a div wrapping five numbered `<p>` items is not a 232-word paragraph
    # — each item is already measured on its own, so counting the wrapper double-counts it.
    candidates = list(_blocks(text))
    candidates += [
        (start, block)
        for start, block in _leaf_divs(text)
        if not re.search(r"<(?:table|tr|p|li)\b", block, re.IGNORECASE)
    ]
    quoted = _quoted_lines(text.splitlines())
    for block_start, block in candidates:
        # A verbatim quote's length is the source's choice, not the writer's. Flagging a
        # 123-word passage lifted from an SEC filing invites paraphrasing the evidence,
        # which is worse than a long sentence.
        if line_of(block_start) in quoted or _is_mostly_quotation(block):
            continue
        visible = " ".join(_strip_tags(block).split())
        if not visible:
            continue
        words = visible.split()
        if len(words) > 120:
            findings.append(
                Finding(
                    "T7",
                    "long paragraph",
                    WARN,
                    line_of(block_start),
                    f"{len(words)} words",
                    "split it, or turn it into a list",
                )
            )
        for sentence in re.split(r"(?<=[.!?])\s+", visible):
            count = len(sentence.split())
            if count > 45:
                findings.append(
                    Finding(
                        "T7",
                        "long sentence",
                        WARN,
                        line_of(block_start),
                        f"{count} words",
                        f"break it up: “{sentence[:60]}…”",
                    )
                )
        bolds = len(re.findall(r"<(?:b|strong)\b", block))
        sentences = max(1, len(re.split(r"(?<=[.!?])\s+", visible)))
        if bolds / sentences > 3:
            findings.append(
                Finding(
                    "T7",
                    "bold overload",
                    WARN,
                    line_of(block_start),
                    f"{bolds} bolds / {sentences} sentences",
                    "when everything is emphasised, nothing is",
                )
            )
    # `<p>` blocks and leaf `<div>`s overlap, so the same sentence can be measured twice.
    out: list[Finding] = []
    for finding in findings:
        key = (finding.rule, finding.line, finding.excerpt)
        if key not in seen:
            seen.add(key)
            out.append(finding)
    return out
