"""Measure the lint against a corpus of real designs, stratified by provenance.

A rule is only meaningfully measured against documents it was supposed to apply to. The
three-tier structure entered `solution-design` on 2026-07-13; five of the designs in this
workspace predate it, so judging them by it measures the wrong thing and produces findings
nobody can act on.

Measured 2026-09-22 against this workspace's own corpus. The counts are deliberately NOT
restated here: this module ships in the public carve and the corpus is private customer work,
so the number of designs a tenant holds is not a fact a public reader needs. Run `--calibrate`
to reproduce them locally — the measurement belongs in the output, not in the source.

The conformant stratum carries **0 errors**, which is the bar a rule must clear to be ERROR at
all. It started at several errors per document and every step down was a defect this harness
found that no unit test could:

* `figure` matched inside "con**figure**d" — a substring marker (29 findings)
* SD12 counted every bullet in the section, not the list its phrase introduced (6 errors)
* the pronoun "one" read as a quantifier, because `[a-z-]{3,}s` also matches a verb (2)
* a list LONGER than stated is usually prose about an external set, not a cut (1)
* `document_kind` called every complete design a fragment — twice, first by counting
  appendix headings and then by matching the omit-from-customer-copy banner that a whole
  design's own A8 contains — nearly half the corpus misclassified
* SD1 and SD2 demanded of a split main file the tiers and appendix sections that live in
  its sibling document, which `solution-design` Step 6 mandates (2 errors)

Two more defects came out of the committed corpus rather than this harness, and are recorded
here because they are the same class and the same lesson:

* `figure` is not a diagram marker. Word-bounding it fixed "con**figure**d" and not the far
  commoner sense — a *number* ("a figure per attribute"). Found by writing the tripwire's
  clean fixture, which tripped SD13 on its own disclaimer. See `catalog.DIAGRAM_REFERENCE`.
* COV-04 could never be absent: its marker was the bare word `solution`, which every
  document titled "Solution design — <account>" satisfies from its own H1. An ERROR-severity
  check that cannot discriminate is not a check (§R18). Found by the mutation suite, which
  could not construct an input where COV-04 failed.
* `\b` is the wrong word boundary for markdown. It counts `_` as a word character, and the
  house style writes a walkthrough as `_How to read this:_` — so `\bhow to read\b` did not
  match it and SD13 fired on the skill's own blank template. Found the third time this
  family bit, after `con**figure**d` and after `figure`-as-a-number.

A fourth and fifth came out of adding the claim rules (SD6-SD9), and they are the same
family as the first three:

* the shared-content-word test that decides two claims are about the same capability was
  satisfied by the product's own name, which appears in nearly every sentence AND nearly
  every matrix row — so SD7's first working shape fired on every conformant design. The
  stoplist is now derived from the document in front of it (`text.topic_terms`), not named.
* `matches_marker` found the word "guardrails" inside a document's own H1, so SD8 drew the
  design's limits from its title. A heading either opens or ends with the marker
  (`catalog.names_section`); asking whether a word *appears* is right for a body and wrong
  for a heading.

SD6 is also a record of what this harness is for. Four shapes were written and measured
before one survived: three of them asked "is this sentence a capability claim?" from a verb
list, and each was mostly wrong on real designs, because these documents are full of
capability verbs that promise nothing — a settings row, a comparison against another
product, a process step whose actor is a human, a glossary definition. The shape that holds
asks nothing about sentences: a table that declares a Status column has already said every
row carries a maturity, so a blank cell in it is a question the document asked itself and
did not answer. It measures 0 on the conformant stratum, which is where an advisory should
sit — it has a negative control in the tripwire corpus and nothing to cry wolf about here.

The rule set is mutation-tested, not only exercised — every guard in it has been deleted
and the suite re-run, scoped with `tests/lint/affected_tests.py` (never hand-picked, which
is what produces a false "0 surviving mutants") and with `__pycache__` cleared between
mutants, because a same-length mutation otherwise reuses stale bytecode. The claim rules'
own pass, 2026-09-22: **32 mutants over `rules_claims`, `catalog`, `text`, `api` and `cli`,
all killed bar one provably equivalent** — adding the document's topic terms to a set that
is then intersected with a topic-free set cannot change the intersection, which is the proof
that the filter it duplicated was redundant, and that filter was deleted. Two other guards
went the same way for the same reason.

That pass is worth recording for what most of its survivors turned out to be. Sixteen of the
thirty-two survived the first run, and only three were code defects: the rest were **tests
of mine that passed for the wrong reason** — a `--strict` case whose fixture produced no
warning to promote, an SD7 quiet-case written with a verb form the rule never matches, a
suppression case whose `lint-ok` comment sat in the wrong section, and a false-positive
regression whose docstring claimed two guards were each load-bearing when either one alone
silenced the input. None of that is visible from a green suite, and none of it would ever
have been caught by CI, because an ADVISORY cannot fail a build: for SD6-SD9 these tests are
not a safety net over the instrument, they *are* the instrument.

Earlier passes covered `rules_coverage`, `rules_integrity` and
`gtm_core.connector_categories`; their record lives with this repo's own change history.
Survivors from every pass became named regressions under "surviving-mutant regressions" in
`tests/lint/test_design_lint.py`.

Nothing here writes a record: the measurement is re-run, not stored (founder decision
2026-09-22). Output carries counts and strata only — never a filename, because the corpus
is real customer work and lives under `content/` for that reason (§R9).
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass
from pathlib import Path

from .api import lint
from .model import ERROR
from .parse import UnparseableDesign, parse_sections
from .rules_coverage import document_kind

CONFORMANT = "conformant"
FRAGMENT = "fragment"
LEGACY = "legacy"
UNREADABLE = "unreadable"

STRATA = (CONFORMANT, FRAGMENT, LEGACY, UNREADABLE)

# The commit that introduced the three-tier read into plugin/skills/solution-design
# (107d3373). A design written before it is not judged by it.
STRUCTURE_LANDED = "2026-07-13"

_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class Measurement:
    counts: dict[str, int]
    by_rule: dict[tuple[str, str], int]
    errors_conformant: int

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def holds(self) -> bool:
        """Whether the ERROR bar was actually measured and actually held.

        An unreadable document makes the measurement incomplete, not clean: the bar would
        otherwise "hold" over a corpus nothing in it could be read from.
        """
        return self.errors_conformant == 0 and not self.counts.get(UNREADABLE, 0)


def document_date(path: Path) -> str:
    match = _DATE.search(path.name)
    return match.group(1) if match else ""


def stratum(path: Path, text: str) -> str:
    """Which stratum a design belongs to. Unreadable input is its own answer, never a pass."""
    try:
        sections = parse_sections(text)
    except UnparseableDesign:
        return UNREADABLE
    if document_kind(sections) == FRAGMENT:
        return FRAGMENT
    date = document_date(path)
    if not date or date < STRUCTURE_LANDED:
        return LEGACY
    return CONFORMANT


def conformant_designs(root: Path) -> list[Path]:
    """Every design in the workspace that the current structure actually governs."""
    out: list[Path] = []
    for path in sorted(root.glob("content/*/accounts/*/solution-design-*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # Not OSError alone: UnicodeDecodeError is a ValueError, so a non-UTF-8 file under
            # content/ used to crash the real-artifact gate instead of being skipped.
            continue
        if stratum(path, text) == CONFORMANT:
            out.append(path)
    return out


def measure(paths: list[Path]) -> Measurement:
    counts: collections.Counter[str] = collections.Counter()
    by_rule: collections.Counter[tuple[str, str]] = collections.Counter()
    errors_conformant = 0
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # UNREADABLE exists for exactly this. A glob can match a directory or a binary,
            # and a corpus measurement that crashes on one is a measurement nobody gets.
            counts[UNREADABLE] += 1
            continue
        where = stratum(path, text)
        counts[where] += 1
        if where == UNREADABLE:
            continue
        for finding in lint(text):
            by_rule[(where, f"{finding.tier} {finding.rule}")] += 1
            if where == CONFORMANT and finding.severity == ERROR:
                errors_conformant += 1
    return Measurement(dict(counts), dict(by_rule), errors_conformant)


def render(m: Measurement) -> str:
    lines = [
        f"corpus: {m.total} documents — "
        + ", ".join(f"{n} {s}" for s, n in sorted(m.counts.items()))
    ]
    for where in STRATA:
        rules = {r: n for (w, r), n in m.by_rule.items() if w == where}
        if not rules:
            continue
        lines.append(f"\n  {where}:")
        for rule, n in sorted(rules.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"    {n:3}  {rule}")
    unreadable = m.counts.get(UNREADABLE, 0)
    bar = "PASS" if m.holds else "FAIL"
    lines.append(
        f"\n  ERROR bar — errors on the conformant stratum: {m.errors_conformant} [{bar}]"
        "\n    a rule may be ERROR only while this is 0; a correct document must lint clean"
    )
    if unreadable:
        # Named on its own line and folded into the verdict. A corpus of one unreadable file
        # otherwise measured "0 errors" and printed PASS — a bar that holds because nothing
        # was weighed, which is the failure this whole harness exists to catch.
        lines.append(
            f"    {unreadable} document(s) could not be read — the bar above was measured over "
            "the rest, so it does not hold for the corpus the glob matched"
        )
    return "\n".join(lines)
