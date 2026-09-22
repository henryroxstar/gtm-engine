from __future__ import annotations

import re

from .catalog import (
    DIAGRAM_MARKERS,
    DIAGRAM_REFERENCE,
    NUMBER_WORDS,
    WALKTHROUGH_MARKERS,
    matches_marker,
)
from .model import ERROR, WARN, Finding, Section
from .parse import UnparseableDesign, parse_sections
from .text import list_after, plain, significant

# ══════════════════════════════════════════════════════════════════════════════════════
# SD11 — contradictory guardrail
# ══════════════════════════════════════════════════════════════════════════════════════
#
# The defect this rule is named after, from the skill body it was written against:
#
#   :528  "Never ship a version log. Solution designs revise silently: no changelog…"
#   :554  "If this is a revision (v2+): append a Version log as the last section"
#   :587  "Revisions carry a Version log … Never ship a v2+ without them."
#
# Twenty-four lines apart, both shipped. Two rules that contradict train the reader to
# ignore both, so this fires at ERROR — and because the judgement is a human's, it reports
# BOTH line numbers and the object they share rather than deciding which one is right.
#
# Precision comes from requiring all three of: a prohibition that actually prohibits an
# action (a negative marker AND a directive verb, so "never mention X" counts but "no
# customer ever saw v1" does not); an affirmative directive carrying no negation at all;
# and at least two shared content words, so two statements about different nouns that
# happen to share one cannot pair.

_DIRECTIVE_VERBS = frozenset(
    """carry carries carrying append appends appending include includes including add adds
    ship ships shipping emit emits emitting write writes writing keep keeps keeping state
    states stating attach attaches provide provides record records recording show shows
    name names naming list lists listing use uses using""".split()
)

_NEGATION = r"(?:never|must\s+not|may\s+not|cannot|can't|do\s+not|don't|should\s+not|shouldn't)"

# The negation must GOVERN the verb, not merely share a sentence with it. "never ship a
# version log" is a prohibition on shipping; "use the product's real name — never a
# hardcoded one" is not a prohibition on using. Allowing up to two words between the two
# covers "must never again ship" without reaching across a clause boundary.
#
# This adjacency requirement is the whole precision story. Without it the rule paired a
# prohibition with any directive that happened to share two nouns: measured on the skill
# body it was written against, it produced 14 findings of which 3 were real. With it, 3
# of 3. A rule at 21% precision is one authors learn to suppress, which is worse than no
# rule because it looks like coverage.
_NEGATED_VERB = re.compile(rf"\b{_NEGATION}\s+(?:\w+\s+){{0,2}}$", re.IGNORECASE)

# Negation also lands AFTER the verb: "carries no changelog" and "ships nothing" are
# prohibitions, not directives. Looking only backwards read them as requirements — caught
# when the fix for the version-log contradiction was itself flagged as contradicting it.
_NEGATED_OBJECT = re.compile(
    r"^\s+(?:\w+\s+){0,1}(?:no|none|nothing|neither|never)\b", re.IGNORECASE
)

# A contradiction is "never X" against "do X", not "never X" against "do Y". But the verbs
# rarely match literally — the defect this rule is named after forbids *shipping* a version
# log and elsewhere requires *carrying* and *appending* one. So verbs are compared by
# class, not by spelling.
_INCLUDE = frozenset(
    """carry carries carrying append appends appending include includes including add adds
    ship ships shipping emit emits emitting write writes writing keep keeps keeping state
    states stating attach attaches provide provides record records recording show shows
    name names naming list lists listing""".split()
)
_USE = frozenset("use uses using".split())
_VERB_CLASSES = (_INCLUDE, _USE)

# A prohibition carrying one of these is SCOPED — it forbids a case, not the action. "Never
# write one profile's events into another's" does not contradict "write the extracted people
# to the per-profile store"; it is the same rule stated twice. Measured on the 73 shipped
# skill bodies, this qualifier plus the verb-class match is what separates a contradiction
# from a correctly-stated exception.
_SCOPED = re.compile(
    r"\b(another|another's|other|others|instead|unless|except|rather\s+than|without|"
    r"elsewhere|different|outside|beyond)\b",
    re.IGNORECASE,
)

_MIN_SHARED_TERMS = 2


def _same_class(forbidden: set[str], asserted: set[str]) -> bool:
    return any((forbidden & klass) and (asserted & klass) for klass in _VERB_CLASSES)


def _split_verbs(sentence: str) -> tuple[set[str], set[str]]:
    """(forbidden, asserted) directive verbs in one sentence.

    Scanned from each verb BACKWARDS to the negation rather than forwards from the
    negation: a forward match is greedy and lands on whichever word the optional gap
    happens to end at, so `never ship a version log` resolved its verb to `log`. Looking
    back from each candidate verb asks the question the rule actually means — is this
    particular verb negated?
    """
    forbidden: set[str] = set()
    asserted: set[str] = set()
    lowered = sentence.lower()
    for m in re.finditer(r"\b[a-z']+\b", lowered):
        verb = m.group(0)
        if verb not in _DIRECTIVE_VERBS:
            continue
        if _NEGATED_VERB.search(lowered[: m.start()]) or _NEGATED_OBJECT.match(lowered[m.end() :]):
            forbidden.add(verb)
        else:
            asserted.add(verb)
    return forbidden, asserted


def _numbered_sentences(text: str) -> list[tuple[int, str]]:
    """Every sentence-ish unit with the 1-based source line it starts on."""
    out: list[tuple[int, str]] = []
    for line_no, raw in enumerate(text.split("\n"), start=1):
        body = plain(raw).strip()
        if not body:
            continue
        for part in re.split(r"(?<=[.!?;:])\s+", body):
            part = part.strip()
            if part:
                out.append((line_no, part))
    return out


def _suppressed_lines(text: str) -> set[int]:
    """Line numbers inside a section carrying `<!-- lint-ok SD11: reason -->`.

    Without this the `--skill` gate had no escape hatch at all — `lint()` filters findings
    through the section suppressions and `lint_skill()` never did, so the one documented way to
    adjudicate a false positive silently did nothing. That matters more here than anywhere else,
    because SD11 is the rule whose own docstring records a residual false-positive class: a
    prohibition whose qualifier sits further away than one sentence, and a restatement of a rule
    the linter reads as its opposite. A gate with a known FP class and no exemption is a gate
    authors learn to route around.

    Scope is the section, matching `lint()`: the comment goes in the block that contains either
    cited line, and it names SD11 and a reason. A body with no headings gets no suppressions —
    refusing to guess a scope is safer than applying one document-wide.
    """
    try:
        sections = parse_sections(text)
    except UnparseableDesign:
        return set()
    lines = text.split("\n")
    out: set[int] = set()
    for n, section in enumerate(sections):
        if "SD11" not in section.suppressed:
            continue
        start = section.line
        end = sections[n + 1].line - 1 if n + 1 < len(sections) else len(lines)
        out.update(range(start, end + 1))
    return out


def lint_skill(text: str) -> list[Finding]:
    """SD11 over a skill body — the `--skill` mode.

    Audits a procedure's own guardrails rather than a design document, the way deck_lint's
    D9 audits a theme source tree rather than a deck. It is the rule that keeps a resolved
    contradiction resolved.

    **Measured 2026-09-22, and the reason this is scoped to a named file.** On
    ``plugin/skills/solution-design/body_template.md``, the body it was written against:
    3 findings, 3 real (the version-log contradiction at L528 against L552, L554 and
    L587) — no false positives, and 0 after the contradiction was resolved. Swept across
    all 73 shipped skill bodies: 72 findings, of which a sample suggests most are not
    contradictions. The residual class is a prohibition whose qualifier sits further away
    than one sentence, so the scope guard cannot see it — and note the corpus count went
    UP (46 → 72) when post-verb negation was fixed, because correctly recognising more
    prohibitions creates more pairs. Corpus precision is not something the current guards
    move; a different rule shape would be needed.

    So SD11 is run against a **named** skill body as part of that skill's Definition of
    Done, which is what it is good at. It is deliberately **not** wired as a corpus-wide
    sweep: at corpus precision it would be a gate authors learn to suppress, and a gate
    people route around is worse than no gate because it looks like coverage.
    """
    exempt = _suppressed_lines(text)
    statements = _numbered_sentences(text)
    prohibitions: list[tuple[int, str, set[str], set[str]]] = []
    directives: list[tuple[int, str, set[str], set[str]]] = []

    for line_no, sentence in statements:
        # The object is what the two statements are compared on, so the verbs are stripped
        # out of it. Otherwise two unrelated sentences pair on "use" and "real".
        forbidden, asserted = _split_verbs(sentence)
        terms = significant(sentence) - _DIRECTIVE_VERBS
        if forbidden:
            if _SCOPED.search(sentence):
                continue  # forbids a case, not the action
            prohibitions.append((line_no, sentence, terms, forbidden))
        elif asserted:
            directives.append((line_no, sentence, terms, asserted))

    out: list[Finding] = []
    seen: set[tuple[int, int]] = set()
    for p_line, p_text, p_terms, p_verbs in prohibitions:
        for d_line, d_text, d_terms, d_verbs in directives:
            if p_line == d_line or not _same_class(p_verbs, d_verbs):
                continue
            shared = p_terms & d_terms
            if len(shared) < _MIN_SHARED_TERMS:
                continue
            key = (min(p_line, d_line), max(p_line, d_line))
            if key in seen or exempt & {p_line, d_line}:
                continue
            seen.add(key)
            obj = ", ".join(sorted(shared)[:4])
            out.append(
                Finding(
                    "SD11",
                    "contradictory guardrail",
                    ERROR,
                    0,
                    f"line {p_line} forbids what line {d_line} requires — both about: {obj}",
                    f"decide which rule is right and delete the other. "
                    f"L{p_line}: {p_text[:80]!r} · L{d_line}: {d_text[:80]!r}",
                )
            )
    return sorted(out, key=lambda f: f.excerpt)


# ══════════════════════════════════════════════════════════════════════════════════════
# SD12 — count drift
# ══════════════════════════════════════════════════════════════════════════════════════
#
# "three capabilities" above a list of two. The failure mode is an edit, not an author:
# merging three bullets into two leaves the count wrong, and the count reads as
# authoritative long after it stopped being true.

# "one" is excluded, and deliberately. It is a pronoun far more often than a quantifier —
# "which **one realises** each leg" and "**one lands** on each leg" both matched, because
# the noun pattern `[a-z-]{3,}s` also matches a third-person verb. Measured on the
# conformant designs, "one" produced 2 of the 3 remaining false errors and zero true ones;
# a stated count of one is not worth checking anyway.
_COUNTABLE = {w: n for w, n in NUMBER_WORDS.items() if n > 1}

_COUNT_PHRASE = re.compile(
    r"\b(?P<n>\d{1,2}|" + "|".join(_COUNTABLE) + r")\s+(?P<noun>[a-z][a-z-]{3,}s)\b[^.\n]*:",
    re.IGNORECASE,
)


def _sd12(sections: list[Section]) -> list[Finding]:
    out: list[Finding] = []
    for section in sections:
        body = plain(section.body)
        for match in _COUNT_PHRASE.finditer(body):
            raw = match.group("n").lower()
            stated = _COUNTABLE.get(raw, int(raw) if raw.isdigit() else 0)
            # The denominator is the list this phrase introduces — not every bullet in the
            # section. A phrase with no list after it cannot be checked: that is
            # `not_verified`, never a finding.
            listed = list_after(body, match.start())
            # Only a SHORTER list is a finding. The failure this rule exists for is a cut:
            # "merging three bullets into two leaves the count wrong". A list LONGER than
            # the stated number is usually prose referring to something outside it — one
            # conformant design reads "walked leg by leg, mapped to the three requirements"
            # above a four-leg list, where the three are an external set and the count is
            # correct. Flagging that taught nothing and would have been suppressed.
            if not stated or not listed or listed >= stated:
                continue
            out.append(
                Finding(
                    "SD12",
                    "count drift",
                    ERROR,
                    section.index,
                    f"“{match.group(0).strip()[:60]}” introduces {listed}, not {stated}",
                    "re-count after every cut — merging two bullets into one leaves the "
                    "number wrong and nothing else notices",
                )
            )
    return out


# ══════════════════════════════════════════════════════════════════════════════════════
# SD13 — a diagram that stands alone
# ══════════════════════════════════════════════════════════════════════════════════════


def _sd13(sections: list[Section]) -> list[Finding]:
    out: list[Finding] = []
    for section in sections:
        drawn = matches_marker(section.body, DIAGRAM_MARKERS) or DIAGRAM_REFERENCE.search(
            section.body
        )
        if not drawn:
            continue
        if matches_marker(section.body, WALKTHROUGH_MARKERS):
            continue
        out.append(
            Finding(
                "SD13",
                "diagram without walkthrough",
                WARN,
                section.index,
                f"“{section.heading[:60]}” draws a diagram and does not read it",
                "every diagram ships with a one-line “how to read this” and a per-component "
                "line in plain language — the target-state diagram especially must not stand alone",
            )
        )
    return out
