"""SD6–SD9 — the claim rules, and the one severity in this linter that never blocks.

SD1–SD4 and SD12–SD13 ask questions about a document's *shape*: is the section there, does
the number match the list, does the diagram get read. Those are decidable, and being wrong
about one is a bug.

SD6–SD9 ask questions about what the design *claims* — is this capability tagged, is a
roadmap feature written as though it ships, does a body claim contradict a stated limit, is
the same action attributed to two different actors. Nothing about those is decidable from
structure alone, and all four share a failure mode the shape rules do not have: **the
author's knowledge is newer than the linter's.** A capability that was Design-target when
these patterns were written ships next sprint; a beta constraint is lifted; a product's
maturity label moves. The linter cannot know, and the SA in the room does.

So all four emit :data:`~gtm_core.design_lint.model.ADVISORY`, and that severity carries
three guarantees, which are the reason it exists rather than reusing WARN:

1. **It never blocks** — not on exit code, and not under ``--strict`` either. A gate people
   route around is worse than no gate, and a judgement call that can fail a build is a
   judgement call somebody will learn to silence.
2. **It is not suppressible from the document.** There is no ``lint-ok SD6`` to write,
   because these never demanded anything in the first place. The alternative — annotating a
   customer-facing solution design with linter comments to record that the linter is out of
   date — puts our tooling's staleness in the customer's copy. The report is the place to
   disagree with an advisory; the deliverable is not.
3. **It prints in its own block, one line per finding**, addressed to a person, with the
   reason the linter might be wrong stated next to the finding rather than left implicit.

These four were specified at ERROR when they were designed. Shipping them that way is the
one thing this module deliberately does not do, decided 2026-09-22: four ERROR-severity
natural-language judgements would have made the SA chain's only deterministic gate depend on
how fresh a regex is.

Each rule's vocabulary lives in :mod:`~gtm_core.design_lint.catalog` and is narrow on
purpose — see the note there on why ``issues``, ``signs``, ``blocks`` and ``audits`` are not
capability verbs in this document family.
"""

from __future__ import annotations

import re

from .catalog import (
    ACTOR_VERBS,
    CAPABILITY_CLAIM,
    CONDITIONAL,
    CONSTRAINT_MARKERS,
    EMPTY_CELL,
    FORWARD_TAG,
    FREEZE_MARKERS,
    GLOSSARY_MARKERS,
    MATURITY_TAG,
    MUTATING_CLAIM,
    OPERATOR_ACTORS,
    PRINCIPAL_ACTORS,
    STATUS_HEADER,
    names_section,
)
from .model import ADVISORY, Finding, Section
from .rules_integrity import _MIN_SHARED_TERMS
from .text import claim_units, plain, significant, topic_terms

__all__ = ["_sd6", "_sd7", "_sd8", "_sd9"]

#: A document that tags nothing at all gets ONE finding, not one per claim — but only once
#: it is making enough claims for the silence to mean something. Two capability sentences in
#: a short design is not a convention nobody followed; it is a short design.
_CONVENTION_FLOOR = 3


#: A question is never a claim. "Whether caller-auth populates the policy input" names a
#: capability verb and promises nothing — it is the design saying it does not know yet, which
#: is the opposite of an untagged claim. Every one of these in the corpus was a false positive.
_INTERROGATIVE = re.compile(
    r"\?|^\W*(?:whether|can|could|does|do|should|is\s+it|are\s+there|which|what|who|how)\b",
    re.IGNORECASE,
)


#: A capability verb inside a relative clause is DEFINING a noun, not promising anything:
#: "a credential **that authorises** an agent", "a registry of which DIDs **are authorised**".
#: This is grammar, not a word list — every definitional false positive on the real corpus
#: had the verb governed by `that` / `which` / `who`.
_RELATIVE_CLAUSE = re.compile(r"\b(?:that|which|who)\s+(?:\w+\s+){0,1}$", re.IGNORECASE)

#: A cell containing an assignment is configuration, not a claim: "Expected Issuer + JWKS =
#: <the issuer>" states a setting. It is the row's *value*, and a value makes no promise.
_ASSIGNMENT = re.compile(r"\S\s*=\s*\S")


def _is_claim(unit: str) -> bool:
    """Whether a unit PROMISES a capability, rather than mentioning one.

    A capability verb is necessary and nowhere near sufficient — measured against this
    workspace's corpus, most units carrying one promised nothing. Three shapes account for
    all of them, and each is excluded on a structural fact rather than a keyword:

    * an **open question** ("Whether caller-auth populates the policy input") — the design
      saying it does not know yet, which is the opposite of an untagged claim;
    * a **definition** ("a credential that authorises an agent") — the verb is governed by a
      relative pronoun, so it describes a noun rather than asserting behaviour;
    * a **configuration row** ("Expected Issuer + JWKS = …") — a setting, not a promise.
    """
    stripped = unit.strip()
    if _INTERROGATIVE.search(stripped) or _ASSIGNMENT.search(stripped):
        return False
    for match in CAPABILITY_CLAIM.finditer(stripped):
        if not _RELATIVE_CLAUSE.search(stripped[: match.start()]):
            return True
    return False


#: How much of a unit to quote back. Long enough to recognise the sentence, short enough
#: that a block of these stays readable on one screen.
_QUOTE = 90


def _quote(unit: str) -> str:
    collapsed = " ".join(unit.split())
    return collapsed if len(collapsed) <= _QUOTE else collapsed[: _QUOTE - 1] + "…"


# ══════════════════════════════════════════════════════════════════════════════════════
# SD6 — a capability claim with no maturity tag
# ══════════════════════════════════════════════════════════════════════════════════════
#
# `solution-design` Step 1: "Claim only what's enforced. Tag every capability the design
# promises as Enforced, Simulated, or Design-target". Its own Definition of Done asks it
# back: "is every capability tagged, and is nothing Design-target shown as live?"
#
# Two shapes, because the two failures are different sizes. Within a section that already
# tags, an untagged claim is DRIFT — the author set the convention here and one row escaped,
# which is a real and small fix. Across a document that tags nothing, there is no drift to
# find; there is a convention that was never applied, and that is one observation about the
# document, not N observations about its sentences.


def _scopes(units: list[str]) -> list[list[str]]:
    """A section's units split into the blocks a tagging convention can be established IN.

    A table is its own scope, and each run of prose between tables is another. This is the
    fix for SD6's last false-positive class and the only one that was structural rather than
    lexical: a section holding a capability matrix AND a settings table was compared as one
    population, so three tagged rows in the matrix made an untagged *setting* read as drift.
    A maturity convention lives in a table, because a table is what has a Status column.
    """
    blocks: list[list[str]] = []
    for unit in units:
        kind = unit.startswith("|")
        if blocks and (blocks[-1][0].startswith("|")) == kind:
            blocks[-1].append(unit)
        else:
            blocks.append([unit])
    return blocks


def _cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _status_column(header: str) -> int | None:
    """The index of the maturity column a table declares in its own header, if it has one."""
    for n, cell in enumerate(_cells(header)):
        if STATUS_HEADER.search(cell):
            return n
    return None


def _untagged_rows(block: list[str]) -> tuple[list[str], int, int] | None:
    """(untagged rows, tagged count, column index) for a capability matrix, else None.

    This is the whole precision story for SD6, and it took four shapes to get here. Every
    earlier one asked "is this sentence a capability claim?" from a verb list, and measured
    against this workspace's corpus every one of them was mostly wrong: these documents are
    full of capability verbs that promise nothing — a settings row ("Set to X — rejects
    forged identity"), a row comparing somebody else's product, a process step whose actor
    is a human, a glossary definition.

    A table that declares a Status column has already answered the question. Every body row
    of it is a capability the design is tagging, the cell either carries a maturity word or
    it does not, and that is structure rather than judgement. Tables with no such column are
    not capability matrices and are not this rule's business.
    """
    if not block[0].startswith("|"):
        return None
    # No row-count floor, and a surviving mutant is why: one was there, and it could not
    # change any answer. With a single body row the row is either filled (nothing untagged)
    # or blank (nothing tagged), and both already return None below. A guard that cannot
    # change an outcome is not a guard — it is a claim about the rule that is not true.
    column = _status_column(block[0])
    if column is None:
        return None
    untagged: list[str] = []
    tagged = 0
    for row in block[1:]:
        cells = _cells(row)
        cell = cells[column] if column < len(cells) else ""
        # EMPTY, not "not one of our words". Measured against this workspace's corpus, a real
        # Status column's vocabulary is far richer than any tag list: "Out of scope → <lane>",
        # "V2 — not reachable in V1", "Conditional — requires <thing>", "App-mediated". Every
        # one of those is an author answering the question, and demanding one of three
        # approved words instead flagged all of them. An author who filled the cell in has
        # tagged the capability; the only thing this rule can honestly say is that a cell is
        # blank while its neighbours are not.
        if EMPTY_CELL.match(cell):
            untagged.append(row)
        else:
            tagged += 1
    return (untagged, tagged, column) if untagged and tagged else None


def _sd6(sections: list[Section]) -> list[Finding]:
    out: list[Finding] = []
    total_claims = 0
    tagged_anywhere = False

    for section in sections:
        if names_section(section.slug, GLOSSARY_MARKERS):
            # A glossary defines terms. SD4 already owns whether one exists; nothing inside
            # it is a promise about the build.
            continue
        units = claim_units(section.body)
        total_claims += len([u for u in units if _is_claim(u)])
        tagged_anywhere = tagged_anywhere or any(MATURITY_TAG.search(u) for u in units)
        for block in _scopes(units):
            found = _untagged_rows(block)
            if not found:
                continue
            untagged, tagged, column = found
            header = _cells(block[0])[column]
            out.append(
                Finding(
                    "SD6",
                    "untagged capability",
                    ADVISORY,
                    section.index,
                    f"{len(untagged)} row(s) leave “{header}” blank where {tagged} "
                    f"fill it in — first: “{_quote(untagged[0])}”",
                    "tag it Enforced / Simulated / Design-target like its neighbours. An "
                    "untagged row in a tagged matrix reads as live to a customer's architect. "
                    "The linter is reading the cell, not the product — if this shipped since, "
                    "the cell is the stale half",
                )
            )

    # No matrix to drift from, and nothing tagged anywhere: there is no convention that was
    # broken, there is one that was never applied. That is a single observation about the
    # document, not one per sentence — so it uses the looser `_is_claim` test, whose
    # imprecision costs little when the answer is one line and the input is unambiguous
    # (the document tags NOTHING). Exercised by the corpus fixtures rather than by real
    # designs: every design in this workspace tags something, so its silence there is not
    # evidence it works.
    if not tagged_anywhere and total_claims >= _CONVENTION_FLOOR:
        out.append(
            Finding(
                "SD6",
                "untagged capability",
                ADVISORY,
                0,
                f"{total_claims} capability claims and no maturity tag anywhere in the document",
                "tag each capability Enforced / Simulated / Design-target at first mention — "
                "an untagged claim reads as live to a customer's architect, whatever was meant",
            )
        )
    return out


# ══════════════════════════════════════════════════════════════════════════════════════
# SD7 — a design-target in the matrix, live in the prose
# ══════════════════════════════════════════════════════════════════════════════════════
#
# `solution-design`: "present design-targets as roadmap, never as live". `solution-scope-check`
# §4b sets the grammar — "Hypothesis / roadmap / joint → conditional — never indicative".
# This is the highest-consequence thing a solution design can get wrong: a customer's
# architect reads an indicative sentence as a commitment.
#
# The first shape written for this rule asked whether a forward-tagged unit was itself
# written in the indicative, and excluded table rows on the grounds that a matrix's tag
# column is its own mood marker. Measured against this workspace's corpus that turned out
# to exclude almost the entire input: nearly every forward tag in real designs lives in a
# capability matrix. A rule whose exclusion removes its own input cannot discriminate, and
# §R18 says that is not a check.
#
# The measurement also named the real failure. The matrix is usually honest — it is the
# NARRATIVE that overclaims. A row says Design-target and three pages earlier the Tier 1
# prose says the thing works. So the rule reads the matrix as the document's own statement
# of maturity and holds the prose to it: a forward-tagged row, and an untagged indicative
# prose claim about the same capability. Both halves come from the document; nothing is
# assumed about the product.


def _units(sections: list[Section]) -> list[str]:
    return [u for s in sections for u in claim_units(s.body)]


def _row_name(row: str) -> str:
    """A capability matrix's first cell — the capability's NAME.

    The row's identity is its name, not its prose. Comparing whole rows is what let the
    product's own name carry the match; comparing names asks the question a human asks,
    which is whether the matrix and the narrative are talking about the same capability.
    """
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    return cells[0] if cells else ""


def _forward_subjects(sections: list[Section], topic: set[str]) -> list[tuple[int, str, set[str]]]:
    """(section index, row text, identifying terms) per forward-tagged capability row.

    A row whose name is entirely topic vocabulary is dropped rather than matched loosely:
    there is nothing left to tell it apart from every other row, and a comparison with no
    discriminating term is the false-positive class this whole rule was rebuilt around.
    """
    out: list[tuple[int, str, set[str]]] = []
    for section in sections:
        for unit in claim_units(section.body):
            if not unit.startswith("|") or not FORWARD_TAG.search(unit):
                continue
            identity = significant(_row_name(unit)) - topic
            if identity:
                out.append((section.index, unit, identity))
    return out


def _sd7(sections: list[Section]) -> list[Finding]:
    topic = topic_terms(_units(sections))
    rows = _forward_subjects(sections, topic)
    if not rows:
        return []
    out: list[Finding] = []
    seen: set[tuple[int, str]] = set()
    for section in sections:
        for unit in claim_units(section.body):
            if unit.startswith("|") or MATURITY_TAG.search(unit):
                continue
            if not CAPABILITY_CLAIM.search(unit) or CONDITIONAL.search(unit):
                continue
            # Not `- topic`: `row_terms` is already topic-free, so subtracting here cannot
            # change the intersection. The filter belongs on the identity, once.
            terms = significant(unit)
            for row_section, row_text, row_terms in rows:
                shared = terms & row_terms
                # The whole NAME must be present for a multi-word capability; a one-word
                # name needs its one word. Anything looser is the product-name match again.
                if len(shared) < min(_MIN_SHARED_TERMS, len(row_terms)):
                    continue
                key = (section.index, unit)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    Finding(
                        "SD7",
                        "design-target as live",
                        ADVISORY,
                        section.index,
                        f"stated in the present tense, while section {row_section} labels it "
                        f"ahead of the build — both about: {', '.join(sorted(shared)[:4])} — "
                        f"“{_quote(unit)}”",
                        "move the prose to the conditional — “would”, “once built”, “planned "
                        "for” — or carry the tag into the sentence. If it shipped since, the "
                        f"matrix row is the stale half: “{_quote(row_text)}”",
                    )
                )
    return out


# ══════════════════════════════════════════════════════════════════════════════════════
# SD8 — a claim that writes to something the document froze
# ══════════════════════════════════════════════════════════════════════════════════════
#
# The Constraints section records what is fixed and not ours to choose. A body claim that
# contradicts one is worse than an unsourced claim, because the document carries its own
# refutation and a reader who finds it stops trusting the rest of the page.
#
# This rule was first built on SD11's prohibition/directive pairing, which was the wrong
# instrument twice over, and the corpus said so both times. SD11 is tuned for skill
# guardrails, where a prohibition carries a negated directive verb ("never ship a version
# log"); no sentence in any real Constraints section has that shape, so the rule could not
# fire. And its section detector asked `matches_marker`, which found the word "guardrails"
# inside a document's own title and drew "limits" from an H1 — see `catalog.names_section`.
#
# What a design actually does is FREEZE an object ("the clinical record system is read-only
# to us", "the schedulers stay in place"), so the contradiction is a later sentence that
# MUTATES the same object. Read verbs are not contradictions: reading a read-only system
# obeys the constraint. Only a write breaks it.


def _frozen(sections: list[Section], topic: set[str]) -> list[tuple[Section, str, set[str]]]:
    """(section, sentence, terms) for every limit statement in a constraint-bearing section.

    Terms are topic-filtered for the reason `text.topic_terms` records: two sentences in one
    design share the product's name whether or not they are about the same object.
    """
    out: list[tuple[Section, str, set[str]]] = []
    for section in sections:
        if not names_section(section.slug, CONSTRAINT_MARKERS):
            continue
        for sentence in _statements(section):
            if _INTERROGATIVE.search(sentence.strip()):
                continue  # an open question states no limit
            if FREEZE_MARKERS.search(sentence):
                terms = significant(sentence) - topic
                if terms:
                    out.append((section, sentence, terms))
    return out


def _statements(section: Section) -> list[str]:
    out: list[str] = []
    for raw in plain(section.body).split("\n"):
        for part in re.split(r"(?<=[.!?;:])\s+", raw.strip()):
            if part.strip():
                out.append(part.strip())
    return out


def _sd8(sections: list[Section]) -> list[Finding]:
    topic = topic_terms(_units(sections))
    limits = _frozen(sections, topic)
    if not limits:
        return []
    out: list[Finding] = []
    seen: set[tuple[int, str]] = set()
    for section in sections:
        if names_section(section.slug, CONSTRAINT_MARKERS):
            continue
        for sentence in _statements(section):
            if _INTERROGATIVE.search(sentence.strip()):
                # An open question claims nothing. "whether the weekend change window is
                # long enough" was the clean fixture's own false positive.
                continue
            if not MUTATING_CLAIM.search(sentence) or FREEZE_MARKERS.search(sentence):
                continue
            terms = significant(sentence)  # `limit_terms` is already topic-free
            for limit_section, limit_text, limit_terms in limits:
                shared = terms & limit_terms
                if len(shared) < _MIN_SHARED_TERMS:
                    continue
                key = (section.index, sentence)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    Finding(
                        "SD8",
                        "constraint contradiction",
                        ADVISORY,
                        section.index,
                        f"this writes to something “{limit_section.heading[:40]}” freezes — "
                        f"both about: {', '.join(sorted(shared)[:4])} — “{_quote(sentence)}”",
                        f"one of the two moved. Either the limit lifted (update “"
                        f"{limit_section.heading[:40]}”) or the claim overreaches (cut it, or "
                        f"say who writes instead). The stated limit reads: “{_quote(limit_text)}”",
                    )
                )
    return out


# ══════════════════════════════════════════════════════════════════════════════════════
# SD9 — the same action attributed to two different actors
# ══════════════════════════════════════════════════════════════════════════════════════
#
# `solution-design` Step 2 pins an actor / operator / principal line and then requires the
# roles be held consistent through every section, because "operator-as-actor in one place
# and principal-as-actor in another is the #1 review finding".
#
# The naive reading of that — flag a document containing both families — is wrong and would
# fire on every correct design, since a design describes both sides of an integration and
# must. The defect is narrower: ONE action, attributed to both. We revoke the credential in
# A4; the holder revokes it in A7. Both cannot be the cryptographic actor, one of the two
# sentences is wrong, and the reader has no way to tell which — which is exactly why it
# survives review often enough to be the #1 finding.


def _actor_family(phrase: str) -> str:
    return "operator" if phrase in OPERATOR_ACTORS else "principal"


_ACTOR_ALT = "|".join(
    re.escape(a) for a in sorted(OPERATOR_ACTORS | PRINCIPAL_ACTORS, key=len, reverse=True)
)
_VERB_ALT = "|".join(re.escape(v) for v in sorted(ACTOR_VERBS, key=len, reverse=True))

# Up to two words between subject and verb covers "we then also revoke", but the gap may
# not contain a clause breaker: in "the customer that we onboard signs the request" the
# verb belongs to a different subject, and a gap-blind match reads the customer as signing.
_CLAUSE_BREAK = frozenset(
    """that which who whom whose and or but if when while because so then than as to of in
    on at by for with from before after unless until""".split()
)

_ACTOR_ACTION = re.compile(
    rf"\b(?P<actor>{_ACTOR_ALT})\b\s+(?P<gap>(?:\w+\s+){{0,2}})(?P<verb>{_VERB_ALT})\b",
    re.IGNORECASE,
)


def _lemma(verb: str) -> str:
    """`verifies` → `verify`, `revokes` → `revoke`. Two spellings of one action must not
    read as two actions, or the rule sees drift wherever a subject changes number."""
    if verb.endswith("ies"):
        return verb[:-3] + "y"
    for cut in (2, 1):
        if verb.endswith(("es", "s")) and verb[:-cut] in ACTOR_VERBS:
            return verb[:-cut]
    return verb


def _sd9(sections: list[Section]) -> list[Finding]:
    # lemma -> family -> (section index, quoted unit). First sighting per family is enough:
    # the finding is that there are two, not how many times each recurs.
    seen: dict[str, dict[str, tuple[int, str]]] = {}
    for section in sections:
        for unit in claim_units(section.body):
            for match in _ACTOR_ACTION.finditer(unit):
                gap = match.group("gap").lower().split()
                if any(word in _CLAUSE_BREAK for word in gap):
                    continue
                actor = match.group("actor").lower()
                lemma = _lemma(match.group("verb").lower())
                family = _actor_family(actor)
                seen.setdefault(lemma, {}).setdefault(family, (section.index, unit))

    out: list[Finding] = []
    for lemma in sorted(seen):
        families = seen[lemma]
        if len(families) < 2:
            continue
        op_section, op_unit = families["operator"]
        pr_section, pr_unit = families["principal"]
        where = (
            f"section {op_section}"
            if op_section == pr_section
            else f"sections {op_section} and {pr_section}"
        )
        out.append(
            Finding(
                "SD9",
                "actor drift",
                ADVISORY,
                0,
                f"“{lemma}” is done by us and by the principal, in {where} — "
                f"“{_quote(op_unit)}” · “{_quote(pr_unit)}”",
                "say which one acts, and if the operator acts because the tooling cannot yet "
                "make the principal the actor, say that and give the V2 path (Step 2). Two "
                "correct sentences about two different operations read alike to this rule — "
                "if that is the case here, they are worth distinguishing in the prose too",
            )
        )
    return out
