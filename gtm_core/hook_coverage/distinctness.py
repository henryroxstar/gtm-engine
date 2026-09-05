from __future__ import annotations

import re
from dataclasses import dataclass

from .config import (
    JACCARD_MAX,
    MAX_NGRAM_EMAILS,
    NGRAM_N,
    _content_words,
    _hedge_ngram_whitelist,
    _ngrams,
    _norm_tokens,
    parse_spec,
)

# --- is it actually a different argument? -------------------------------------------


@dataclass(frozen=True)
class PairOverlap:
    a: str
    b: str
    jaccard: float

    @property
    def same_argument(self) -> bool:
        return self.jaccard > JACCARD_MAX


def argument_distinctness(specs: dict[str, str]) -> list[PairOverlap]:
    """Pairwise content-word overlap between the opening touch of each spec.

    Touch 1 is the comparison because it is the argument the recipient is actually
    asked to accept; later touches legitimately re-tread it.

    The threshold and the tokeniser are ``outreach_pack_linter``'s own
    (``JACCARD_MAX``, ``_content_words``) rather than new numbers, so this inherits the
    calibration of the live ``same-company-overlap`` ERROR instead of inventing one.
    Above the threshold, two specs are the same argument wearing two subjects.
    """
    opening: dict[str, set[str]] = {}
    for name, text in specs.items():
        touches = parse_spec(text or "")
        first = next((t for t in touches if t.number == 1), touches[0] if touches else None)
        if first is None:
            continue
        words = _content_words(first.body)
        if words:
            opening[name] = words

    names = sorted(opening)
    out: list[PairOverlap] = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            wa, wb = opening[a], opening[b]
            out.append(PairOverlap(a, b, len(wa & wb) / len(wa | wb)))
    return sorted(out, key=lambda p: -p.jaccard)


@dataclass(frozen=True)
class SharedPhrase:
    """One normalised n-gram and the spec bodies carrying it verbatim."""

    phrase: str
    bodies: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.bodies)


#: A line whose only content is merge tags and punctuation — ``{{Why Now}}.`` — or the
#: mandated greeting. Neither carries a word the drafter chose, so neither can be evidence
#: of a shared argument. Deliberately narrow: one authored word anywhere on the line and it
#: counts, so this can never exempt real copy that happens to sit beside a tag.
_MERGE_TAG_RE = re.compile(r"\{\{[^}]*\}\}")
_GREETING_RE = re.compile(r"^\s*(hi|hello|hey)\b[^.!?]*,\s*$", re.IGNORECASE)


def _authored_lines(body: str) -> str:
    """``body`` with merge-tag-only lines and the greeting removed."""
    kept = []
    for line in (body or "").splitlines():
        if _GREETING_RE.match(line):
            continue
        if not re.search(r"[A-Za-z]", _MERGE_TAG_RE.sub(" ", line)):
            continue
        kept.append(line)
    return "\n".join(kept)


def shared_phrases(
    specs: dict[str, str],
    *,
    n: int = NGRAM_N,
    ceiling: int = MAX_NGRAM_EMAILS,
    touch: int | None = 1,
) -> list[SharedPhrase]:
    """Word n-grams appearing verbatim in more than ``ceiling`` of a campaign's specs.

    This is ``outreach_pack_linter``'s existing ``template-share`` ERROR — the 6-gram
    ceiling — evaluated at a scope it has never been applied to. That rule compares the
    emails *inside one pack*; nothing compares the *specs of one campaign* against each
    other, which is precisely where a campaign-wide monoculture lives.

    Measured against the four staged specs, this is the statistic that detects the
    finding and :func:`argument_distinctness` is the one that misses it (see the H0
    baseline). Two specs can restate one claim in different domain nouns — which drives
    bag-of-word overlap *down* while leaving the shared scaffolding intact — so a
    Jaccard threshold cannot separate "same argument, reworded" from "different
    argument". A verbatim shared phrase is not diluted that way.

    Caveat the caller must keep: some shared phrases are legitimate scaffolding (the
    sign-off, a house CTA) rather than a shared argument. Exemplars are returned, not just
    a count, so that distinction stays a human's to make.

    **The one scaffold that is excluded rather than reported** is the mandated opener —
    ``Hi {{First Name}},`` followed by the bare ``{{Why Now}}.`` clause. Those two lines
    contain no authored words at all (see :func:`_authored_lines`), so they are structurally
    incapable of evidencing a shared *argument*, and the skill's beat 1 requires every spec
    to carry them verbatim. Before this, the phrase ``hi first name why now .`` fired on
    every campaign written correctly — a finding that is always true is one readers learn to
    scroll past, which is how a real ``template-share`` hit goes unnoticed.
    """
    whitelist = _hedge_ngram_whitelist()
    bodies: dict[str, str] = {}
    for name, text in specs.items():
        for t in parse_spec(text or ""):
            if touch is None:
                bodies[f"{name}#{t.number}"] = t.body
            elif t.number == touch:
                bodies[name] = t.body

    seen: dict[tuple[str, ...], set[str]] = {}
    for name, body in bodies.items():
        for gram in _ngrams(_norm_tokens(_authored_lines(body)), n):
            if gram in whitelist:
                continue
            seen.setdefault(gram, set()).add(name)

    out = [
        SharedPhrase(" ".join(g), tuple(sorted(names)))
        for g, names in seen.items()
        if len(names) > ceiling
    ]
    return sorted(out, key=lambda p: (-p.count, p.phrase))
