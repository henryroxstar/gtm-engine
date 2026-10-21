from __future__ import annotations

import re

_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_INLINE_CODE = re.compile(r"`[^`]*`")
_TAG = re.compile(r"<[^>]+>")

# Sentence-ish split. Good enough for prose rules and deliberately not a parser: the rules
# that decide `no` all re-check their own object, so an over-eager split costs recall, not
# precision.
_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n")

# Stopwords dropped before two statements are compared for a shared object. Kept short on
# purpose — a longer list starts discarding the nouns the comparison is about.
_STOPWORDS = frozenset(
    """a an the and or but if then than that this these those is are was were be been being
    do does did doing have has had having it its to of in on at by for with from as not no
    never always must should will would can could may might we you your our their them they
    he she his her i me my when while where which who whom what how why all any each every
    same so such only just also very more most other some own out up down over under again
    here there both few many much one two three four five""".split()
)

_WORD = re.compile(r"[a-z][a-z0-9-]{2,}")


def plain(text: str) -> str:
    """Prose with code fences, HTML comments, tags and link targets removed."""
    out = _CODE_FENCE.sub(" ", text)
    out = _HTML_COMMENT.sub(" ", out)
    out = _MD_LINK.sub(r"\1", out)
    out = _INLINE_CODE.sub(" ", out)
    out = _TAG.sub(" ", out)
    return out


def sentences(text: str) -> list[str]:
    """Prose split into sentence-ish units, blanks dropped."""
    return [s.strip() for s in _SENTENCE.split(plain(text)) if s.strip()]


def significant(text: str) -> set[str]:
    """The content words of a statement — what two statements are compared on."""
    return {w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS}


def bullet_count(body: str) -> int:
    """Top-level list items in a body — what a stated count is checked against."""
    n = 0
    for line in plain(body).split("\n"):
        stripped = line.strip()
        if re.match(r"^([-*+]|\d+\.)\s+\S", stripped) and not line.startswith(("  ", "\t")):
            n += 1
    return n


def table_rows(body: str) -> list[str]:
    """Body rows of every markdown table, header and separator rows dropped."""
    out: list[str] = []
    for line in body.split("\n"):
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        if re.fullmatch(r"\|[\s:|-]+\|", stripped):
            continue  # separator
        out.append(stripped)
    return out[1:] if len(out) > 1 else []


_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+\S")


def list_after(body: str, pos: int) -> int:
    """Items in the list that immediately follows `pos`, or 0 if none does.

    SD12 asks whether "three capabilities:" is followed by three things. Counting every
    bullet in the *section* answers a different question and got it wrong on six
    conformant designs — "Three limits worth stating:" sat in a section holding five
    bullets across two lists. Only the list that follows the phrase is its denominator.

    Nested items (indented) belong to their parent and are not counted. A blank line
    between items does not end the list; any other non-list content does.
    """
    tail = body[pos:]
    newline = tail.find("\n")
    if newline == -1:
        return 0
    lines = tail[newline + 1 :].split("\n")

    count = 0
    started = False
    for line in lines:
        if not line.strip():
            if started:
                continue  # a blank line inside a list does not end it
            continue
        if _LIST_ITEM.match(line):
            if not line.startswith(("  ", "\t")):  # top level only
                count += 1
                started = True
            continue
        if started:
            break
        if count == 0 and not started:
            break  # non-list content before any item: no list follows
    return count


def claim_units(body: str) -> list[str]:
    """The smallest pieces of a body that can carry a maturity tag, in document order.

    SD6–SD9 all ask a question about one claim ("is *this* tagged?"), so the unit has to be
    the thing an author would tag: a table row, a top-level bullet, or a sentence of prose.
    Asking the question of a whole section instead answers a different one — a section with
    one tagged row reads as tagged, which is precisely the drift SD6 exists to find.

    A nested bullet folds into nothing of its own: it elaborates its parent, and its parent
    carries the tag.
    """
    units: list[str] = []
    prose: list[str] = []

    def flush() -> None:
        if prose:
            units.extend(sentences(" ".join(prose)))
            prose.clear()

    for line in plain(body).split("\n"):
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if stripped.startswith("|"):
            flush()
            if not re.fullmatch(r"\|[\s:|-]+\|", stripped):
                units.append(stripped)
            continue
        if _LIST_ITEM.match(line):
            flush()
            if not line.startswith(("  ", "\t")):
                units.append(stripped)
            continue
        prose.append(stripped)
    flush()
    return units


#: A term in more than this share of a document's units is what the document is ABOUT, and
#: so tells you nothing about which claim you are looking at. Measured against this
#: workspace's corpus: at this cutoff a design loses under 1% of its vocabulary — the
#: product's own name and the handful of words every sentence carries.
TOPIC_RATIO = 0.10

#: …but only when there are enough units for a share to mean anything. Without the floor a
#: short document has its whole vocabulary declared "topic" and every comparison matches.
TOPIC_FLOOR = 3


def topic_terms(units: list[str], ratio: float = TOPIC_RATIO, floor: int = TOPIC_FLOOR) -> set[str]:
    """The document's own topic vocabulary — terms too common in it to discriminate.

    Two claims sharing content words is the test SD7 and SD8 use to decide they are about
    the same thing, and it fails silently on a document with a dominant noun. Measured
    against this workspace's corpus, SD7's first working shape produced false positives on
    every conformant design, and in every case the shared terms were the product's own
    two-word name: it appears in nearly every sentence and in nearly every matrix row, so
    `>= 2 shared terms` was satisfied before any comparison happened.

    That is the third time this family has bitten here — `org_token` tokenising a subdomain
    as the company, and COV-04's marker being the bare word `solution`, which every document
    titled "Solution design — <account>" satisfies from its own H1. The general fix is to
    derive the stoplist from the document in front of you rather than to name the word.
    """
    # `floor` is what protects a short document: a term must clear an absolute count as
    # well as a share, so in a two-unit body nothing can reach the threshold at all and
    # the whole vocabulary stays comparable.
    threshold = max(len(units) * ratio, floor)
    df: dict[str, int] = {}
    for unit in units:
        for term in significant(unit):
            df[term] = df.get(term, 0) + 1
    return {term for term, n in df.items() if n > threshold}
