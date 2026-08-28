"""Is the message axis actually varied? — measurement, not opinion.

A campaign's copy is written against a *seat* (three of them: security / exec /
technical) while its messaging asset, ``hook-matrix.md``, is written against a
*persona* (ten, for one live tenant: 7 enterprise + 3 startup, each crossed with
6 "why now" signals — 60 cells). The narrower axis wins silently, and the wider one is
never consulted, because ``email-sequence`` *tells* the model to "pick the hook from
the matrix at the persona x signal intersection" and **nothing checks that it did**.

The result, measured across the four staged specs of ``agent-gateway-cross-org``: 397
recipients, one argument. The campaign manifest's own ``[experiment]`` block had
already written that down ("Only one pitch was written") and nothing changed — which
is the whole reason this module exists. An instruction with no check is a suggestion.

This module is the *measurement* half (phase H0). It changes no behaviour and is wired
into no gate; it only makes four questions answerable:

* :func:`parse_matrix` — what cells does the tenant's matrix actually offer?
* :func:`declared_cell` — which cell does a spec claim to implement?
* :func:`argument_distinctness` — are two specs the same argument wearing two subjects?
* :func:`persona_coverage` — which personas hold recipients that no spec addresses?

The design follows :mod:`gtm_core.signal_record`: a judgement call ("did you vary the
message?") becomes reliable only once it is a *declared, checkable property* rather
than prose to be re-read. Nothing here carries hook text of its own — the matrix is
the only source of a hook, and it is the tenant's asset (see ``CLAUDE.md``: no tenant
copy in ``gtm_core/``). What this module hardcodes is a generic *role* vocabulary,
exactly as ``outreach_pack_linter._SEAT_RULES`` already does for seats.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from gtm_core.paths import resolve_knowledge_file, resolve_profiles_root
from gtm_core.prospects_consolidate import _prospects_dir
from gtm_core.signal_record import HOOK_CELL_COLUMN, SIGNAL_COLUMN

# ``gtm_core`` reaching into ``tests/linter`` is deliberate and pre-existing: see
# gtm_core/build_eval_sheet.py and gtm_core/cells.py, which take the same import for
# the same reason. Re-implementing the seat resolver, the spec parser or the overlap
# tokeniser would mean two definitions of one rule, and the copy gate's definition is
# the one that ships.
_LINTER_DIR = Path(__file__).resolve().parent.parent / "tests" / "linter"
if str(_LINTER_DIR) not in sys.path:  # pragma: no cover - import plumbing
    sys.path.insert(0, str(_LINTER_DIR))

from merge_render_linter import parse_spec  # noqa: E402
from outreach_pack_linter import (  # noqa: E402
    JACCARD_MAX,
    MAX_NGRAM_EMAILS,
    NGRAM_N,
    _content_words,
    _hedge_ngram_whitelist,
    _ngrams,
    _norm_tokens,
    persona_of,
    seat_of,
)

from .cells import load_cell_map  # noqa: E402
from .eval_calibration import draft_cell_dirs  # noqa: E402

__all__ = [
    "JACCARD_MAX",
    "MAX_NGRAM_EMAILS",
    "NGRAM_N",
    "SharedPhrase",
    "Cell",
    "Coverage",
    "DeclaredCell",
    "Matrix",
    "MatrixShape",
    "PairOverlap",
    "RowCell",
    "SegmentFit",
    "SignalFit",
    "UnknownHookCell",
    "argument_distinctness",
    "audit_campaign",
    "classify_rows",
    "declared_cell",
    "declared_stakes",
    "derive_row_cell",
    "segment_fit",
    "signal_columns_for_segment",
    "signal_fit",
    "signal_terms",
    "main",
    "parse_matrix",
    "persona_coverage",
    "persona_key_of_label",
    "persona_of",
    "render",
    "shared_phrases",
]

#: A persona needs at least this many enrolled recipients before "no spec addresses
#: it" is a finding rather than a curiosity. Config, not a constant with a view: the
#: PRD has no evidence for a specific number, so the operator supplies it.
MIN_RECIPIENTS = 40

#: How many distinct arguments a campaign should carry. Also the operator's call
#: (PRD section 8 question 1 recommends 4-6); this default only makes the report say
#: something rather than nothing.
MIN_ARGUMENTS = 4

#: Exemplars shown per aggregated finding, matching gtm_core.finding_budget.
EXEMPLARS = 3

#: Share of a spec's recipients that must sit in the declared cell's segment before the
#: aim is credible. No PRD evidence for a specific number; below half means most readers
#: are not in the grid the hook was written for, which is the weakest defensible bar.
#: Operator-tunable (``--min-segment-fit``), like ``MIN_RECIPIENTS``.
MIN_SEGMENT_FIT = 0.5

#: Share of recipients whose recorded signal evidence attests the declared signal.
#: A WARN and not an ERROR: unlike segment -- where both sides are explicit, enumerated
#: values -- this infers a signal from free text, so a miss can mean "the evidence does
#: not say" rather than "the aim is wrong". Reported with the matched terms so the
#: operator can see which reading produced the number.
MIN_SIGNAL_ATTESTATION = 0.25


class MatrixShape:
    """The four shapes ``hook-matrix.md`` takes across the profiles in this repo.

    Deliberately not one parser with three fallbacks: a tenant's matrix is a hand-kept
    document, and the shape it is in tells you what it can express. Only the first
    three can name a (persona, signal) pair at all.
    """

    #: A persona x signal grid: header cell 0 mentions both axes, rows are personas,
    #: columns are signals, every inner cell is a hook. (observed: tenant A)
    GRID = "grid"
    #: One table row per cell, with separate Persona and Signal columns. (observed: tenant B)
    ROWS = "rows"
    #: A section per persona, with ``- **pillar x signal:** "hook"`` bullets.
    #: (observed: tenant C)
    SECTIONS = "sections"
    #: No persona axis and no signal axis anywhere in the file. Not a parser gap: a
    #: table of ``| id | angle | payoff | formats | status |`` carries neither
    #: coordinate, so no implementation can derive a cell from it. Reported by name so
    #: it can never be mistaken for "parsed fine, found nothing" — a gate that passes
    #: by finding nothing is the failure this whole PRD is about. (_template)
    UNSUPPORTED = "unsupported"


class UnknownHookCell(ValueError):
    """A spec declared a cell the matrix does not define.

    A hard error, because the matrix *is* the vocabulary: a spec free to invent its own
    persona or signal can declare conformance to a cell that does not exist, which is
    indistinguishable from declaring nothing.
    """


# --- persona vocabulary ---------------------------------------------------------------
#
# The title -> persona resolver lives in ``outreach_pack_linter`` beside ``_SEAT_RULES``,
# not here. H0 shipped a provisional copy in this module to make "N personas hold
# recipients" measurable at all; H3 promoted it next to the seat rules and this module now
# imports it. Two cue lists for one question drift apart — while both existed, each already
# knew titles the other did not ("Chief AI Officer" resolved to a seat but to no persona).
# :func:`persona_of` is re-exported here so callers of this module need not know that.


def persona_key_of_label(label: str) -> str | None:
    """Normalise a *matrix* persona label onto the same key space as :func:`persona_of`.

    Running one cue list over both a recipient's title and the matrix's own row label
    is what lets the two axes be joined without this module hardcoding any tenant's
    persona names. A label that does not normalise is reported as unmapped, never
    dropped — an unjoinable matrix row is a real gap in the report, not an absence.
    """
    return persona_of(_clean_cell(label))


# --- matrix parsing -----------------------------------------------------------------

_TABLE_ROW_RE = re.compile(r"^\s*\|(?P<body>.*)\|\s*$")
_SEPARATOR_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_HEADING_RE = re.compile(r"^(?P<hashes>#{2,3})\s+(?P<title>.+?)\s*$")
_BULLET_RE = re.compile(
    r"^\s*[-*]\s+\*\*(?P<label>[^*]+?)\s*:?\s*\*\*\s*:?\s*(?P<hook>.*)$",
)
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def _clean_cell(text: str) -> str:
    """One matrix cell or label, normalised for comparison but not for display."""
    out = (text or "").strip()
    out = out.replace("**", "")
    # The "new in role" columns carry a leading U+1F195 (the NEW badge); it is
    # decoration on the axis label, never part of the signal's identity.
    out = re.sub("^\\s*\N{SQUARED NEW}\\s*", "", out)
    return re.sub(r"\s+", " ", out).strip()


def _split_row(line: str) -> list[str] | None:
    m = _TABLE_ROW_RE.match(line)
    if not m or _SEPARATOR_RE.match(line):
        return None
    return [c.strip() for c in m.group("body").split("|")]


@dataclass(frozen=True)
class Cell:
    """One (segment, persona, signal) intersection and the hook text it holds."""

    segment: str
    persona: str
    signal: str
    hook: str
    #: Stable id when the matrix carries one (the ``rows`` shape does). Empty
    #: otherwise — this module never mints an id, because an id that changes breaks
    #: outcome attribution and only the tenant's file can promise stability.
    argument_id: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.segment, self.persona, self.signal)


@dataclass
class Matrix:
    """A parsed ``hook-matrix.md``: what cells the tenant actually offers."""

    shape: str
    cells: dict[tuple[str, str, str], Cell] = field(default_factory=dict)
    #: Why an ``UNSUPPORTED`` matrix could not be read, in the operator's words.
    reason: str = ""
    path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.shape != MatrixShape.UNSUPPORTED

    @property
    def personas(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(c.persona for c in self.cells.values()))

    @property
    def signals(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(c.signal for c in self.cells.values()))

    @property
    def segments(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(c.segment for c in self.cells.values()))

    def persona_keys(self) -> dict[str, str]:
        """``{persona label -> canonical key}`` for every label that normalises."""
        out = {}
        for label in self.personas:
            key = persona_key_of_label(label)
            if key:
                out[label] = key
        return out

    def unmapped_personas(self) -> tuple[str, ...]:
        mapped = self.persona_keys()
        return tuple(p for p in self.personas if p not in mapped)

    def find(self, persona: str, signal: str) -> Cell | None:
        """A cell by persona and signal, matched case- and segment-insensitively.

        Segment is not part of the lookup because a spec declares the pair a reader
        experiences (persona x signal); which table it came from is the matrix's own
        filing system, not part of the claim.
        """
        want_p, want_s = _clean_cell(persona).lower(), _clean_cell(signal).lower()
        for cell in self.cells.values():
            if cell.persona.lower() == want_p and cell.signal.lower() == want_s:
                return cell
        return None


def _sections(text: str) -> list[tuple[str, str]]:
    """``[(heading, body)]`` for every ``##``/``###`` section, plus a leading ``""``."""
    body = _FRONTMATTER_RE.sub("", text)
    out: list[tuple[str, str]] = []
    current, buf = "", []
    for line in body.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            out.append((current, "\n".join(buf)))
            current, buf = _clean_cell(m.group("title")), []
        else:
            buf.append(line)
    out.append((current, "\n".join(buf)))
    return out


def _parse_grid_section(segment: str, body: str) -> list[Cell]:
    """A persona x signal grid: row label is the persona, column head is the signal."""
    lines = body.splitlines()
    cells: list[Cell] = []
    signals: list[str] = []
    for line in lines:
        row = _split_row(line)
        if row is None:
            continue
        if not signals:
            # First non-separator row of the table is the header.
            signals = [_clean_cell(c) for c in row[1:]]
            continue
        persona = _clean_cell(row[0])
        if not persona:
            continue
        for signal, hook in zip(signals, row[1:], strict=False):
            hook_text = _clean_cell(hook)
            if signal and hook_text:
                cells.append(Cell(segment or "default", persona, signal, hook_text))
    return cells


def _column_index(header: list[str], *needles: str) -> int | None:
    for i, cell in enumerate(header):
        low = _clean_cell(cell).lower()
        if any(n in low for n in needles):
            return i
    return None


def _parse_rows_section(segment: str, body: str) -> list[Cell]:
    """One table row per cell, with separate Persona / Signal / hook columns."""
    cells: list[Cell] = []
    header: list[str] | None = None
    idx: dict[str, int | None] = {}
    for line in body.splitlines():
        row = _split_row(line)
        if row is None:
            continue
        if header is None:
            header = row
            idx = {
                "persona": _column_index(row, "persona"),
                "signal": _column_index(row, "signal"),
                "hook": _column_index(row, "hook", "angle"),
                "id": _column_index(row, "id"),
            }
            continue
        if idx["persona"] is None or idx["signal"] is None or idx["hook"] is None:
            continue
        try:
            persona = _clean_cell(row[idx["persona"]])
            signal = _clean_cell(row[idx["signal"]])
            hook = _clean_cell(row[idx["hook"]])
        except IndexError:  # pragma: no cover - ragged hand-edited row
            continue
        arg_id = ""
        if idx["id"] is not None and idx["id"] < len(row):
            arg_id = _clean_cell(row[idx["id"]])
        if persona and signal and hook:
            cells.append(Cell(segment or "default", persona, signal, hook, arg_id))
    return cells


def _parse_sections_shape(text: str) -> list[Cell]:
    """A section per persona, with ``- **pillar x signal:** "hook"`` bullets."""
    cells: list[Cell] = []
    for heading, body in _sections(text):
        if not heading:
            continue
        bullets = _join_bullets(body)
        for label, hook in bullets:
            segment, _, signal = label.partition("×") if "×" in label else label.partition(" x ")
            segment, signal = _clean_cell(segment), _clean_cell(signal)
            if not signal:
                # A bullet with no "pillar x signal" label carries no signal
                # coordinate; it is prose under a persona heading, not a cell.
                continue
            hook_text = _clean_cell(hook)
            if hook_text:
                cells.append(Cell(segment or "default", heading, signal, hook_text))
    return cells


def _join_bullets(body: str) -> list[tuple[str, str]]:
    """``[(label, hook)]``, folding a bullet's indented continuation lines into it."""
    out: list[tuple[str, str]] = []
    for line in body.splitlines():
        m = _BULLET_RE.match(line)
        if m:
            out.append((m.group("label").strip(), m.group("hook").strip()))
        elif out and line.strip() and line[:1].isspace():
            label, hook = out[-1]
            out[-1] = (label, f"{hook} {line.strip()}".strip())
    return out


def _detect_shape(text: str) -> str:
    for _heading, body in _sections(text):
        for line in body.splitlines():
            row = _split_row(line)
            if row is None:
                continue
            first = _clean_cell(row[0]).lower()
            if "persona" in first and "signal" in first:
                return MatrixShape.GRID
            has_persona = _column_index(row, "persona") is not None
            has_signal = _column_index(row, "signal") is not None
            if has_persona and has_signal:
                return MatrixShape.ROWS
            break  # only the first table row of a section is a header
    for heading, body in _sections(text):
        if heading and _join_bullets(body):
            return MatrixShape.SECTIONS
    return MatrixShape.UNSUPPORTED


def parse_matrix(source: str | Path) -> Matrix:
    """Read a ``hook-matrix.md`` into ``{(segment, persona, signal) -> Cell}``.

    Accepts a path or the document text. Carries no hook of its own: every hook string
    in the result came out of the file, which is the tenant's asset and the only place
    a hook may be written (``CLAUDE.md``).
    """
    path: Path | None = None
    if isinstance(source, Path):
        path, text = source, source.read_text(encoding="utf-8")
    elif "\n" not in source and Path(source).is_file():
        path = Path(source)
        text = path.read_text(encoding="utf-8")
    else:
        text = source

    shape = _detect_shape(text)
    if shape == MatrixShape.UNSUPPORTED:
        return Matrix(
            shape=shape,
            reason=(
                "no persona axis and no signal axis in this file — a hook bank of "
                "angles cannot name a persona x signal cell, so hook coverage is not "
                "measurable against it (add Persona and Signal columns to gate it)"
            ),
            path=path,
        )

    cells: list[Cell] = []
    if shape == MatrixShape.SECTIONS:
        cells = _parse_sections_shape(text)
    else:
        parse = _parse_grid_section if shape == MatrixShape.GRID else _parse_rows_section
        for heading, body in _sections(text):
            cells.extend(parse(heading, body))

    return Matrix(shape=shape, cells={c.key: c for c in cells}, path=path)


# --- what a spec declares -----------------------------------------------------------

_HOOK_CELL_RE = re.compile(r"^hook_cell:\s*(?P<value>.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_ARGUMENT_ID_RE = re.compile(r"^argument_id:\s*(?P<value>.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_PREMISE_RE = re.compile(r"^premise:\s*(?P<value>.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_STAKES_RE = re.compile(r"^stakes:\s*(?P<value>.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_CAPABILITY_RE = re.compile(r"^capability:\s*(?P<value>.+?)\s*$", re.MULTILINE | re.IGNORECASE)


@dataclass(frozen=True)
class DeclaredCell:
    persona: str
    signal: str
    argument_id: str = ""
    raw: str = ""


def declared_cell(spec_text: str, matrix: Matrix | None = None) -> DeclaredCell | None:
    """The matrix cell a spec claims to implement, or None if it declares none.

    Read with one regex per field over the spec's fenced ``Key: value`` front block —
    the same single-line read ``merge_render_linter`` already uses for ``Sign-off``,
    so the field costs no new parser.

    Pass ``matrix`` to *validate* the declaration: an unknown persona or signal raises
    :class:`UnknownHookCell`, because a spec free to invent its own coordinates can
    declare conformance to a cell that does not exist. Called without a matrix this is
    a pure read, which is what lets the H0 baseline report "0 of 4 specs declare a
    cell" instead of crashing on the four specs that predate the field.
    """
    m = _HOOK_CELL_RE.search(spec_text or "")
    if not m:
        return None
    raw = m.group("value").strip()
    persona, sep, signal = raw.partition("×") if "×" in raw else raw.partition(" x ")
    if not sep:
        persona, signal = raw, ""
    persona, signal = _clean_cell(persona), _clean_cell(signal)
    am = _ARGUMENT_ID_RE.search(spec_text or "")
    declared = DeclaredCell(
        persona=persona,
        signal=signal,
        argument_id=(am.group("value").strip() if am else ""),
        raw=raw,
    )
    if matrix is not None and matrix.ok and matrix.find(persona, signal) is None:
        raise UnknownHookCell(
            f"hook_cell {raw!r} is not a cell in the matrix "
            f"({len(matrix.cells)} cells across {len(matrix.personas)} personas)"
        )
    return declared


# --- does the row's own fact ESTABLISH what the body claims? --------------------------
#
# The dominant defect class in the 2026-08-21 operator review, and the one nothing checked.
# Six of twelve rejections were a version of the same sentence:
#
#     "single internal tool (Navigator) - doesn't establish the multi-framework credential
#      pain the follow-on paragraph claims"
#     "single vendor product (ARKA Group GEOINT software) - no multi-framework signal"
#     "fact is one Autodesk product on one platform (Flow Studio) - doesn't establish the
#      'crosses more than one platform' claim that follows"
#
# Note what these are NOT. They are not off-topic (`signal-off-topic` passes them — the fact
# is about exactly the right subject). They are not contradictions (`signal-contradicts-pitch`
# passes them — the fact does not announce the capability). They are not thin (`specificity`
# passes them — the render is full of anchors). The fact is true, relevant and specific, and
# the body's next paragraph asserts something ARITHMETICALLY larger than it: the body claims
# plurality, the evidence attests one thing. Two paragraphs that visibly do not connect, which
# is the fastest "this is generated" tell a recipient gets.
#
# So it becomes a declared property, the same move `hook_cell` already made: the spec states
# the premise its body requires, the profile states what attests each premise, and a row whose
# own recorded evidence cannot meet the bar is a type error rather than a judgement call.
#
# The vocabulary is the TENANT's (`knowledge/premise-vocab.toml`) — this module carries no
# premise text of its own, exactly as it carries no hook text. A missing file disables the
# check, the same convention `--case-study-file` and `competitors.toml` already use.

_PREMISE_VOCAB_FILE = "premise-vocab.toml"


@dataclass(frozen=True)
class Premise:
    """One entry from the tenant's ``premise-vocab.toml``.

    ``min_distinct`` is the arity the body's claim needs. A premise asserting that the
    recipient runs agents across SEVERAL frameworks needs two distinct framework names in
    evidence; a premise asserting they run agents at all needs one. That number is the whole
    check — everything else is term matching.
    """

    key: str
    min_distinct: int
    terms: frozenset[str]
    claim: str = ""

    def attested_by(self, text: str) -> set[str]:
        """Which of this premise's terms the text carries, as distinct terms."""
        low = (text or "").lower()
        hits = set()
        for term in self.terms:
            # Word-boundary match so "sap" does not fire inside "sapphire". Terms may be
            # multi-word ("copilot studio"), which \b still handles correctly at both ends.
            if re.search(rf"\b{re.escape(term)}\b", low):
                hits.add(term)
        return hits

    def met_by(self, text: str) -> bool:
        return len(self.attested_by(text)) >= self.min_distinct


def load_premise_vocab(profile: str, profiles_root: Path | None = None) -> dict[str, Premise]:
    """Read the tenant's premise vocabulary, or ``{}`` when it ships none.

    Schema (``schema = 1``)::

        [premise.multi-framework]
        claim        = "the recipient runs agents on more than one framework"
        min_distinct = 2
        terms        = ["langgraph", "autogen", "crewai", "bedrock", ...]

    Returns ``{}`` for a missing or malformed file rather than raising: this check is
    opt-in per profile, and a tenant that has not written the file simply does not get it.
    """
    root = profiles_root or resolve_profiles_root()
    path = root / profile / "knowledge" / _PREMISE_VOCAB_FILE
    if not path.is_file():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out: dict[str, Premise] = {}
    for key, entry in (data.get("premise") or {}).items():
        terms = frozenset(
            str(t).strip().lower() for t in (entry.get("terms") or []) if str(t).strip()
        )
        if not terms:
            continue
        out[key.strip().lower()] = Premise(
            key=key.strip().lower(),
            min_distinct=max(1, int(entry.get("min_distinct", 1))),
            terms=terms,
            claim=str(entry.get("claim") or "").strip(),
        )
    return out


def declared_premise(spec_text: str) -> str:
    """The premise id a spec declares, or ``""``. Same one-line front-block read as
    :func:`declared_cell`, so the field costs no new parser."""
    m = _PREMISE_RE.search(spec_text or "")
    return m.group("value").strip().lower() if m else ""


def declared_stakes(spec_text: str) -> str:
    """The consequence a spec claims its body attaches to the gap it names, or ``""``.

    Same one-line front-block read as :func:`declared_premise`, and it exists for the same
    reason that one does: to turn a property no regex can judge into one a gate can.

    ``voice.md`` job 4 says *"a gap with no consequence is trivia"*, and nothing enforced it.
    Both 2026-08-21 ship30 specs shipped a named gap with no cost attached, and both of their
    "what changed" sections **claimed the opposite** — security said "the stakes name a limit,
    not a feeling", exec said the why "explains why it is HARD". The operator rejected on
    exactly that, in the same words, for the second round running: *"we say what is missing
    but never what it costs them. Nothing is at stake, so there is no reason to reply."*

    A prose claim in §2 is unfalsifiable. A declared field is not: once the consequence is
    written down as ``stakes:``, ``lint_stakes`` can prove the body actually carries it, and
    the eval sheet can show the labeler the specific claim to judge rather than asking for a
    general verdict on the email. Unlike ``premise`` this value is free text, not a key into
    a vocabulary file — the consequence is per-argument and there is no fixed set of them.
    Lower-casing is deliberate: attestation is checked case-insensitively so re-capitalising
    a sentence in the body is not a lint failure.
    """
    m = _STAKES_RE.search(spec_text or "")
    return m.group("value").strip() if m else ""


def capability_slug(label: str) -> str:
    """``"Credentials & delegation"`` -> ``"credentials-delegation"``.

    One normaliser for both sides, so a spec may declare either the taxonomy's own label or
    its slug and still land on the same group.
    """
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", (label or "").lower())).strip("-")


def capability_vocab(profile: str, profiles_root: Path | None = None) -> dict[str, str]:
    """``slug -> label`` for the profile's capability taxonomy, read from ``product.md``.

    The vocabulary is **tenant knowledge**, never a list in this module — the same rule
    ``hook_cell`` follows against ``hook-matrix.md``. A profile whose ``product.md`` has no
    taxonomy section returns ``{}``, which turns the check off rather than failing every
    spec: a tenant that has not written a taxonomy has not declared a violation.
    """
    try:
        path = resolve_knowledge_file(
            profiles_root or resolve_profiles_root(), profile, "product.md"
        )
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return {}
    out: dict[str, str] = {}
    for title, body in _sections(text):
        if "capability taxonomy" not in title.lower():
            continue
        for line in body.splitlines():
            row = _split_row(line)
            if not row:
                continue
            label = _clean_cell(row[0])
            slug = capability_slug(label)
            # The header row ("Group") and the separator normalise to noise, not a group.
            if slug and slug != "group" and len(row) > 1:
                out.setdefault(slug, label)
    return out


#: How many specs in one campaign may argue the same capability group before it is one
#: argument in several costumes. Two is deliberate, not one: a campaign legitimately runs
#: the same capability at two different seats (a CISO and a Chief Risk seat both meet the
#: attribution gap), and forbidding that would push drafters into contrived arguments —
#: the failure this rule exists to prevent, arrived at from the other side.
MAX_SPECS_PER_CAPABILITY = 2


def declared_capability(spec_text: str, vocab: dict[str, str] | None = None) -> str:
    """The capability group a spec declares, normalised to its slug, or ``""``.

    Raises :class:`ValueError` when ``vocab`` is supplied and the declared value is not in
    it. An unvalidated field cannot be counted: ``credentials_delegation`` and
    ``credentials-delegation`` are one group to a reader and two to a ``Counter``, so a typo
    would silently buy a spec an extra slot under the cap.
    """
    m = _CAPABILITY_RE.search(spec_text or "")
    if not m:
        return ""
    slug = capability_slug(m.group("value"))
    if vocab and slug not in vocab:
        known = ", ".join(sorted(vocab)[:EXEMPLARS])
        raise ValueError(
            f"capability {m.group('value').strip()!r} is not a group in product.md's "
            f"capability taxonomy (known: {known})"
        )
    return slug


def capability_monotone(
    capabilities: dict[str, str], cap: int = MAX_SPECS_PER_CAPABILITY
) -> list[str]:
    """Findings for capability groups more than ``cap`` specs in this campaign argue from.

    **Why this is a campaign-level rule and not a linter rule.** Every gate in
    ``merge_render_linter`` judges one spec against its own rows; none of them can see a
    sibling, so none of them can see the defect that matters most. On 2026-08-23 seven
    drafted specs each passed the per-email gate at zero errors while six of the seven made
    the *same* argument — the operator's verdict on the batch was that they all looked the
    same. Monotone is only visible from above.

    Specs that declare nothing are not counted. The field is new, so an undeclared spec is
    an unmigrated one, not a violation — :func:`audit_campaign` reports those separately as
    an advisory ``capability-undeclared``.
    """
    counts = Counter(slug for slug in capabilities.values() if slug)
    out = []
    for slug, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        if n > cap:
            specs = ", ".join(sorted(s for s, c in capabilities.items() if c == slug)[:EXEMPLARS])
            out.append(
                f"argument-monotone: {n} specs argue capability {slug!r} (cap {cap}) — "
                f"{specs}; one argument in {n} costumes reads as mass mail, "
                f"re-angle one onto a group product.md's pain->owner table gives this seat"
            )
    return out


@dataclass(frozen=True)
class PremiseFinding:
    email: str
    premise: str
    attested: tuple[str, ...]
    needed: int

    @property
    def detail(self) -> str:
        got = ", ".join(sorted(self.attested)) or "nothing"
        return (
            f"premise {self.premise!r} needs {self.needed} distinct attesting term(s); "
            f"this row's evidence carries {len(self.attested)} ({got})"
        )


def premise_attestation(
    rows: list[dict],
    premise: Premise,
    *,
    evidence_fields: tuple[str, ...] = ("signal_evidence", "signal_clause", "why_now"),
) -> Counter:
    """``{term -> how many rows attest the premise SOLELY via that term}``.

    The companion measurement to :func:`premise_unsupported`, and the one that says whether a
    pass means anything. A premise with ``min_distinct = 1`` is satisfied by a single word,
    so if that word is common the gate is a word-presence check wearing an entailment check's
    name — which is the error the premise rule was created to catch, committed by the rule.

    Measured 2026-08-23 on the shipped enterprise-security list: all 15 rows attested
    ``cross-org-agents`` and **11 of them on the bare word "partner"**, where it described a
    commercial relationship (an integrator *joining* a partner network, a hospital *selecting*
    a vendor) rather than agents crossing an organisational boundary. The premise's own claim
    is about agents crossing; nothing in a lone "partner" establishes that.

    Reported, never auto-corrected: the term list and ``min_distinct`` live in the tenant's
    ``premise-vocab.toml``, so tightening them is the operator's call about their own
    messaging, not a change code should make silently.
    """
    solo: Counter = Counter()
    for r in rows:
        text = " ".join(str(r.get(f) or "") for f in evidence_fields)
        # Same name-stripping and same `attested_by` the gate itself uses. A second
        # implementation of "what does this row attest" would drift from the first, and then
        # this function would be reporting on a check nobody runs.
        company = str(r.get("company") or "").strip()
        if company:
            text = re.sub(re.escape(company), " ", text, flags=re.IGNORECASE)
        hits = premise.attested_by(text)
        if len(hits) == 1:
            solo[sorted(hits)[0]] += 1
    return solo


def premise_unsupported(
    rows: list[dict],
    premise: Premise,
    *,
    evidence_fields: tuple[str, ...] = ("signal_evidence", "signal_clause", "why_now"),
) -> list[PremiseFinding]:
    """Rows whose own research record cannot carry the premise the spec declares.

    Reads the row's RECORDED evidence, never the rendered body: the question is whether the
    research found enough to justify the claim, and a body that asserts it regardless is
    precisely the defect. Scanning `signal_evidence` first (the verbatim source span) plus
    the clause and the raw notes gives the row every chance to attest before it fails —
    a false ERROR here deletes a good row, which is the expensive direction.
    """
    out: list[PremiseFinding] = []
    for r in rows:
        text = " ".join(str(r.get(f) or "") for f in evidence_fields)
        # Strip the account's own name before matching. A company literally called
        # "<X> Partners", "<X> Ecosystem" or "<X> Vendors" would otherwise attest a premise
        # on its letterhead — the evidence would carry the term without the FACT carrying it,
        # which is precisely the "relevance is not entailment" error this rule exists to catch,
        # committed by the rule itself. Caught 2026-08-21 by reading a render ("Ardent Health
        # Partners"); that row's attestation turned out to be genuine (its why_now names an
        # announced enterprise partnership), so this changes no live verdict — it closes the
        # hazard before a row passes on its name alone.
        company = str(r.get("company") or "").strip()
        if company:
            text = re.sub(re.escape(company), " ", text, flags=re.IGNORECASE)
        hits = premise.attested_by(text)
        if len(hits) < premise.min_distinct:
            out.append(
                PremiseFinding(
                    email=(r.get("email") or "?").strip(),
                    premise=premise.key,
                    attested=tuple(sorted(hits)),
                    needed=premise.min_distinct,
                )
            )
    return out


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


# --- the campaign-level report ------------------------------------------------------


@dataclass
class Coverage:
    """What one campaign's message axis actually looks like."""

    campaign: str = ""
    profile: str = ""
    matrix: Matrix | None = None
    #: (sequence_id, csv, spec) triples the campaign is built from.
    sources: list[dict] = field(default_factory=list)
    rows: int = 0
    seats: Counter = field(default_factory=Counter)
    personas: Counter = field(default_factory=Counter)
    #: Titles the provisional resolver could not place, with counts. Named, never
    #: hidden: this is the honest size of what the report cannot see.
    unresolved: Counter = field(default_factory=Counter)
    #: Rows whose title DOES resolve to a matrix persona, but not to any cell in the row's
    #: OWN segment grid -- e.g. a `CEO / Founder` title (Startup-only in the matrix) on an
    #: enterprise row. Keyed ``"{persona}/{segment}"``. Distinct from `unresolved` (no
    #: persona at all): together the two are every row this campaign's matrix cannot place,
    #: which is what `render()`'s "unassignable" line sums. This is a fact about the LIST
    #: and the MATRIX, not a defect in any one email, so it is reported here rather than as
    #: a linter rule -- fixing it is the operator's call (widen the matrix or the resolver,
    #: or drop the row), never something `gtm_core` can correct on its own.
    unassignable: Counter = field(default_factory=Counter)
    declared: dict[str, DeclaredCell | None] = field(default_factory=dict)
    #: spec -> the capability group its beat 3 argues from, ``""`` when undeclared. The
    #: distribution across this dict is what ``argument-monotone`` counts.
    capabilities: dict[str, str] = field(default_factory=dict)
    #: Drafted (never-staged) cells folded into this audit, by spec name. Reported so a
    #: reader can tell which findings are about live copy and which about a pilot.
    drafts: set[str] = field(default_factory=set)
    #: spec -> (Counter of the cell each ROW recorded at research time, rows scanned).
    #: Empty counter with a non-zero count means the list predates ``hook_cell`` on the row.
    row_cells: dict[str, tuple[Counter, int]] = field(default_factory=dict)
    overlaps: list[PairOverlap] = field(default_factory=list)
    shared: list[SharedPhrase] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    #: Advisory findings that do NOT set ``failed``. Kept as a separate list rather than
    #: a level field on one list, so that every existing caller of ``findings`` keeps its
    #: meaning: everything in it still fails the run.
    warnings: list[str] = field(default_factory=list)
    #: Per-spec list-vs-cell fit, keyed by spec filename.
    segment_fits: dict[str, SegmentFit] = field(default_factory=dict)
    signal_fits: dict[str, SignalFit] = field(default_factory=dict)
    min_recipients: int = MIN_RECIPIENTS
    min_arguments: int = MIN_ARGUMENTS
    min_segment_fit: float = MIN_SEGMENT_FIT
    min_signal_attestation: float = MIN_SIGNAL_ATTESTATION

    @property
    def specs(self) -> int:
        return len(self.declared)

    @property
    def declared_count(self) -> int:
        return sum(1 for d in self.declared.values() if d is not None)

    @property
    def arguments(self) -> int:
        """Distinct arguments shipped: declared cells, else distinguishable specs.

        Before any spec declares a cell (today) the only evidence of an argument is
        the copy itself, so fall back to counting specs no sibling duplicates. That is
        deliberately generous — and it still returns 1 for this campaign.
        """
        ids = {
            d.argument_id or f"{d.persona} x {d.signal}"
            for d in self.declared.values()
            if d is not None
        }
        if ids:
            return len(ids)
        if not self.declared:
            return 0
        # Undeclared: fall back to the copy itself. A phrase carried verbatim by
        # EVERY spec means one scaffold and, on the evidence of the 2026-08-18 set,
        # one argument -- the case bag-of-word overlap scores as distinct.
        if any(p.count >= len(self.declared) for p in self.shared):
            return 1
        duplicated = {p.b for p in self.overlaps if p.same_argument}
        return max(len(self.declared) - len(duplicated), 1)

    @property
    def unresolved_rows(self) -> int:
        return sum(self.unresolved.values())

    @property
    def unassignable_rows(self) -> int:
        """Total rows no matrix cell can hold: unresolved persona + wrong-grid persona."""
        return self.unresolved_rows + sum(self.unassignable.values())

    @property
    def failed(self) -> bool:
        return bool(self.findings)


def persona_coverage(
    personas: Counter,
    declared: dict[str, DeclaredCell | None],
    matrix: Matrix | None = None,
    *,
    min_recipients: int = MIN_RECIPIENTS,
) -> list[str]:
    """Which personas hold enough recipients to matter and have no spec addressing them.

    One aggregated finding naming the personas, not one per persona — a campaign
    missing six personas should report a rate with exemplars, the
    :mod:`gtm_core.finding_budget` contract, rather than printing six walls. Findings
    are formatted ``"rule: subject — detail"`` so ``split_rule`` reads them unchanged.
    """
    addressed = set()
    for d in declared.values():
        if d is None:
            continue
        key = persona_key_of_label(d.persona)
        addressed.add(key or d.persona.lower())

    populated = [(p, n) for p, n in personas.most_common() if n >= min_recipients]
    missing = [(p, n) for p, n in populated if p not in addressed]
    if not missing:
        return []
    shown = ", ".join(f"{p} ({n})" for p, n in missing[:EXEMPLARS])
    more = f", +{len(missing) - EXEMPLARS} more" if len(missing) > EXEMPLARS else ""
    return [
        f"persona-unaddressed: {len(missing)} of {len(populated)} populated persona(s) "
        f"— no spec addresses {shown}{more} "
        f"(>= {min_recipients} recipients each)"
    ]


# --- does the LIST match the cell? ----------------------------------------------------
#
# Everything above this line checks a spec against the matrix, or specs against each
# other. Nothing checks a spec against the people it is actually sent to -- which is how
# four specs can declare four valid, distinct cells and still be aimed at the wrong grid.
# Measured 2026-08-21 on `agent-gateway-cross-org`: the builder spec declares
# `CTO / Founding Engineer x MCP / A2A in build`, a cell that exists, whose copy matches
# it, applied to a list that is 43/61 (70%) enterprise while that persona is in the
# matrix's STARTUP grid. `lint_hook_cell`, `argument_distinctness` and `persona_coverage`
# all pass on it.


def _norm_segment(value: str) -> str:
    """Canonical segment spelling for either side of the comparison.

    ``merge_hygiene.clean_segment`` owns the vocabulary; this module must not hold a
    second copy of it, because a comparison that normalises differently from the writer
    is a comparison that silently stops matching.

    The two sides arrive in different shapes and BOTH have to land on the same string:
    a CSV cell is the bare value (``"Enterprise"``), while the matrix's segment is its
    section heading verbatim (``"Enterprise (per ICP value-prop ranking)"``). So after
    the CSV-shaped normalisation fails, fall back to finding a canonical segment among
    the heading's own tokens. Without this the check reported 0% fit for all four specs
    on 2026-08-21 — including the architect list, which is 18/18 correctly aimed.
    """
    from .merge_hygiene import SEGMENTS, clean_segment

    cleaned = clean_segment(value).lower()
    if cleaned in SEGMENTS:
        return cleaned
    tokens = set(re.split(r"[^a-z0-9]+", cleaned))
    found = [s for s in SEGMENTS if s in tokens]
    # Exactly one, or the heading is ambiguous and guessing would be worse than failing
    # the comparison openly.
    return found[0] if len(found) == 1 else cleaned


#: Words too common to distinguish one signal label from another.
_SIGNAL_STOPWORDS = frozenset(
    """a an and are as at be by for from has have in into is it its of on or that the to
    with new first next own per via when where which while who your their our""".split()
)


@dataclass(frozen=True)
class SegmentFit:
    """How much of a spec's list sits in the segment its declared cell belongs to."""

    spec: str
    declared_segment: str
    counts: Counter = field(default_factory=Counter)

    @property
    def rows(self) -> int:
        return sum(self.counts.values())

    @property
    def matched(self) -> int:
        return self.counts.get(self.declared_segment, 0)

    @property
    def share(self) -> float:
        return self.matched / self.rows if self.rows else 0.0

    def failed(self, threshold: float = MIN_SEGMENT_FIT) -> bool:
        # An unknown declared segment is not a pass: it means the comparison could not
        # be made, and a check that passes by being unable to look is the failure mode
        # this whole module exists to close.
        return bool(self.rows) and self.share < threshold


@dataclass(frozen=True)
class SignalFit:
    """How much of a spec's list carries evidence for its declared signal."""

    spec: str
    signal: str
    rows: int = 0
    attested: int = 0
    terms: tuple[str, ...] = ()
    #: Which distinctive terms actually matched, most common first — the reason the
    #: number is what it is, so a WARN can be judged rather than merely obeyed.
    hits: Counter = field(default_factory=Counter)
    #: Terms dropped as non-discriminating because they matched more than
    #: :data:`SIGNAL_TERM_NOISE_SHARE` of the rows. Reported, never silently discarded: a
    #: label whose terms are ALL noise is unmeasurable, and the operator has to be able to
    #: see that rather than read a suspiciously healthy attestation number.
    noise_terms: tuple[str, ...] = ()
    #: Rows that attested via list-wide terms ONLY. The measure that matters: a label can
    #: yield a discriminating term that happens to match almost nothing, so counting terms
    #: says little and counting ROWS says everything.
    noise_only: int = 0

    @property
    def share(self) -> float:
        return self.attested / self.rows if self.rows else 0.0

    @property
    def noise_dominated(self) -> bool:
        """Every term this label yields is corpus-wide, so its attestation number is not
        evidence about this signal.

        **Reported, not subtracted.** Removing noise terms from the match was tried first and
        was wrong: a genuine 100% attestation and a spurious one are indistinguishable by
        frequency alone, so filtering silently zeroed a fixture where 57 rows really did all
        attest ``mcp``. Telling the two apart needs a background corpus this function does not
        receive. What it CAN do is say so — a high score carried entirely by list-wide words is
        a number the operator must not read as coverage, and the fix belongs in the matrix
        label (or a research pass), not in a threshold."""
        # Two earlier definitions of this were inert on the very draw it was written for, and
        # a positive control caught both. "Are all the terms noise" failed because the hiring
        # label also yields `coe` and `hiring` — clean terms that matched almost nothing.
        # "Did any clean term match at all" failed because exactly one row of nine matched
        # `coe`, so a single lucky hit vouched for eight rows that had none.
        #
        # Counting ROWS is what holds: 8 of 9 attesting rows rested on `agent`/`platform`
        # alone. A majority resting on list-wide words means the score describes the list, not
        # the signal, whatever the term inventory looks like.
        return bool(self.attested) and self.noise_only > self.attested / 2

    def failed(self, threshold: float = MIN_SIGNAL_ATTESTATION) -> bool:
        return bool(self.rows and self.terms) and self.share < threshold


def signal_terms(matrix: Matrix) -> dict[str, frozenset[str]]:
    """``{signal label -> the terms that best identify it}``.

    Derived from the matrix's own column labels, never from a term list held here: the
    hook matrix is the tenant's asset and ``gtm_core`` carries no tenant vocabulary
    (``CLAUDE.md``). Two rules, both learned from getting this wrong on 2026-08-21:

    **Acronyms beat words.** A label's UPPERCASE tokens (``MCP``, ``A2A``, ``AP2``) name
    the thing; its lowercase ones (``entering``, ``architecture``, ``build``) are
    connective English that matches any sentence. When a label has uppercase tokens,
    only those are used. The first version ignored casing and scored
    ``MCP / A2A in build`` on the word "build", reporting 7/61 attestation from rows
    that never mentioned an agent protocol.

    **Distinctiveness is scoped to the segment grid, not the whole matrix.** ``mcp`` and
    ``a2a`` appear in both an enterprise and a startup column; a matrix-wide uniqueness
    rule deletes them from both and keeps only the generic remainder — discarding the
    strongest terms available. Within one grid they are unique, which is the question
    that matters: did this list attest THIS column rather than a sibling column.
    """
    by_segment: dict[str, list[str]] = {}
    for cell in matrix.cells.values():
        by_segment.setdefault(cell.segment, [])
        if cell.signal not in by_segment[cell.segment]:
            by_segment[cell.segment].append(cell.signal)

    out: dict[str, frozenset[str]] = {}
    for labels in by_segment.values():
        tokenised = {}
        for label in labels:
            raw = [t for t in re.split(r"[^A-Za-z0-9]+", label) if len(t) >= 3]
            strong = {t.lower() for t in raw if t.isupper()}
            weak = {
                t.lower() for t in raw if not t.isupper() and t.lower() not in _SIGNAL_STOPWORDS
            }
            tokenised[label] = strong or weak
        seen = Counter(t for tokens in tokenised.values() for t in tokens)
        for label, tokens in tokenised.items():
            # A term shared with a sibling column in the SAME grid cannot tell the two
            # apart, so it is dropped -- but only against siblings, never matrix-wide.
            distinctive = frozenset(t for t in tokens if seen[t] == 1)
            out[label] = distinctive or frozenset(tokens)
    return out


def segment_fit(
    spec: str,
    declared: DeclaredCell | None,
    matrix: Matrix | None,
    segments: Counter,
) -> SegmentFit | None:
    """Compare a spec's recipients against the segment of the cell it declares.

    Returns ``None`` when the comparison cannot be made at all -- no declaration, no
    usable matrix, or a cell the matrix does not place in a segment. That is reported
    by its own existing finding (``hook-cell-missing`` / ``hook-cell-unknown``) rather
    than being restated here as a fit failure.
    """
    if declared is None or matrix is None or not matrix.ok:
        return None
    cell = matrix.find(declared.persona, declared.signal)
    if cell is None or not cell.segment:
        return None
    # Accumulate, never rebuild as a dict comprehension: normalisation MERGES keys
    # ("Enterprise" and "enterprise" both land on "enterprise"), and a comprehension
    # silently keeps whichever came last instead of summing them. That bug reported
    # builder as 60% startup on a list that is 70% enterprise -- the check inverting
    # the very defect it was written to catch.
    counts: Counter = Counter()
    for value, n in segments.items():
        if value:
            counts[_norm_segment(value)] += n
    return SegmentFit(
        spec=spec,
        declared_segment=_norm_segment(cell.segment),
        counts=counts,
    )


#: A term matching more than this share of the candidate rows is not evidence for any
#: particular signal — it describes the list. Same reasoning and same number as
#: `outreach_pack_linter.SOFT_ANCHORS`'s 2026-08-21 recalibration ("fires on >40% of a live
#: list = describes the list, doesn't screen it").
SIGNAL_TERM_NOISE_SHARE = 0.40


def _discriminating_terms(
    terms: frozenset[str], lowered_rows: list[str]
) -> tuple[frozenset[str], frozenset[str]]:
    """Split a signal's terms into the ones that discriminate and the ones that are noise.

    ``signal_terms`` derives its terms from the matrix's own column label, which works when
    the label is acronym-shaped (``MCP/A2A/AP2 entering architecture`` -> ``mcp, a2a, ap2``)
    and fails when it is ordinary English. ``Hiring for "agent platform" / AI CoE`` reduces to
    ``agent, coe, hiring, platform`` — and in an agent-identity prospect list, ``agent`` and
    ``platform`` appear nearly everywhere. Measured 2026-08-23 on a real per-cell draw: 8 of 9
    rows "attested" the hiring signal on those two words alone, while read as prose **not one
    was about hiring or a centre of excellence**. The 25% attestation threshold was satisfied
    entirely by noise, and the cell reported coverage it did not have.

    A cross-label check is not enough on its own — it catches ``platform`` and ``partner``,
    which appear in two column labels each, but misses ``agent``, because the other labels
    spell it ``agents`` and ``agentic``. Frequency in the corpus being matched is the measure
    that actually works, because it asks the right question: does this term separate these
    rows from the rest of the list, or does it describe the whole list?
    """
    if not lowered_rows:
        return terms, frozenset()
    ceiling = SIGNAL_TERM_NOISE_SHARE * len(lowered_rows)
    keep, noise = set(), set()
    for term in terms:
        pattern = re.compile(rf"\b{re.escape(term)}\b")
        matches = sum(1 for row in lowered_rows if pattern.search(row))
        (noise if matches > ceiling else keep).add(term)
    return frozenset(keep), frozenset(noise)


def signal_fit(
    spec: str,
    declared: DeclaredCell | None,
    matrix: Matrix | None,
    evidence: list[str],
) -> SignalFit | None:
    """Count recipients whose recorded evidence attests the spec's declared signal.

    ``evidence`` is one joined string per row (clause + evidence + why-now): whichever
    field the researcher happened to write the signal into should count, since the
    question is whether the SIGNAL was observed, not which column holds it.
    """
    if declared is None or matrix is None or not matrix.ok:
        return None
    cell = matrix.find(declared.persona, declared.signal)
    if cell is None:
        return None
    terms = signal_terms(matrix).get(cell.signal, frozenset())
    lowered_rows = [text.lower() for text in evidence]
    _, noise = _discriminating_terms(terms, lowered_rows)
    hits: Counter = Counter()
    attested = 0
    noise_only = 0
    for lowered in lowered_rows:
        matched = [t for t in terms if re.search(rf"\b{re.escape(t)}\b", lowered)]
        if matched:
            attested += 1
            hits.update(matched)
            if not set(matched) - noise:
                noise_only += 1
    return SignalFit(
        spec=spec,
        signal=cell.signal,
        rows=len(evidence),
        attested=attested,
        terms=tuple(sorted(terms)),
        hits=hits,
        noise_terms=tuple(sorted(noise)),
        noise_only=noise_only,
    )


# --- what a ROW itself has, derived rather than hand-typed ---------------------------
#
# Everything above this line either reads what a SPEC declares or infers a row's signal
# heuristically from free text (`signal_fit`'s term matching, proven noise-dominated on a
# real draw -- see `_discriminating_terms`'s docstring). Nothing turns a row's own recorded
# facts into its half of a cell. `HOOK_CELL_COLUMN` exists for exactly this and is unpopulated
# on every live row (measured 2026-08-23: 0/207), because writing it by hand duplicates the
# persona a title already implies and drifts from it the moment nobody keeps the two in sync.
#
# The persona half of a cell is already derivable (`persona_of(title)`) and the segment half
# is already an enumerated column, so the ONLY atom worth recording is the signal -- which
# `signal_fit` cannot reliably infer from prose. `derive_row_cell` turns
# `persona_of(title) x segment x SIGNAL_COLUMN` into a `Cell`, so `HOOK_CELL_COLUMN` becomes a
# derivation instead of a second hand-typed fact that can disagree with the title.


def signal_columns_for_segment(matrix: Matrix, segment: str) -> tuple[str, ...]:
    """The closed vocabulary of signal labels valid for one segment's grid.

    Scoped to the row's OWN grid, never matrix-wide: ``MCP/A2A/AP2 entering architecture``
    (enterprise) and ``MCP / A2A in build`` (startup) are different columns that happen to
    share vocabulary, and a row cannot attest a signal that only exists in the other grid's
    persona x signal space -- that is precisely the class of mis-declaration
    ``cell-segment-fit`` already measures at the spec level (see ``PENDING.md``, builder
    2026-08-21: a valid cell declared onto the wrong grid for 70% of its own list).
    """
    norm = _norm_segment(segment)
    out: list[str] = []
    for cell in matrix.cells.values():
        if _norm_segment(cell.segment) != norm:
            continue
        if cell.signal not in out:
            out.append(cell.signal)
    return tuple(out)


@dataclass(frozen=True)
class RowCell:
    """One row's own persona x segment x signal, and the cell it resolves to, or why not.

    ``reason`` is populated whenever ``cell`` is ``None`` -- never a bare, unexplained
    ``None`` -- because a silent miss here is indistinguishable from "this row is fine and
    just has no signal yet", which is the exact ambiguity ``HOOK_CELL_COLUMN``'s own
    docstring warns a hand-typed column can hide.
    """

    email: str
    persona_key: str | None
    segment: str
    signal_column: str
    cell: Cell | None
    reason: str = ""


def derive_row_cell(
    matrix: Matrix,
    *,
    email: str,
    title: str,
    segment: str,
    signal_column: str,
) -> RowCell:
    """Derive one row's matrix cell from its own title, segment and recorded signal.

    The row-level counterpart to :func:`declared_cell`: that reads what a SPEC claims;
    this reads what the ROW itself has. Fails closed rather than guessing -- an
    unresolved title, a blank signal, or a persona x signal pair that exists in the
    matrix but not in THIS row's segment grid all return ``cell=None`` with a reason
    naming which half failed, never a best-effort pick.
    """
    persona_key = persona_of(title)
    norm_segment = _norm_segment(segment)
    clean_signal = _clean_cell(signal_column)

    if persona_key is None:
        return RowCell(
            email,
            None,
            norm_segment,
            clean_signal,
            None,
            reason="title does not resolve to a matrix persona",
        )
    if not clean_signal:
        return RowCell(
            email,
            persona_key,
            norm_segment,
            clean_signal,
            None,
            reason=f"no {SIGNAL_COLUMN!r} recorded",
        )

    candidates = [
        c
        for c in matrix.cells.values()
        if persona_key_of_label(c.persona) == persona_key
        and _norm_segment(c.segment) == norm_segment
        and c.signal.lower() == clean_signal.lower()
    ]
    if not candidates:
        # Distinguish "this signal belongs to a DIFFERENT grid" from "this persona has no
        # such signal anywhere" -- the first is a segment mistake, the second is a typo or
        # an invented column, and the operator needs to know which to fix.
        in_other_grid = any(
            persona_key_of_label(c.persona) == persona_key
            and c.signal.lower() == clean_signal.lower()
            for c in matrix.cells.values()
        )
        reason = (
            f"{persona_key!r} x {clean_signal!r} exists but not in the {norm_segment!r} grid"
            if in_other_grid
            else f"no {norm_segment!r}-grid cell for persona {persona_key!r} x signal {clean_signal!r}"
        )
        return RowCell(email, persona_key, norm_segment, clean_signal, None, reason=reason)
    if len(candidates) > 1:
        # Should be unreachable if matrix persona labels are unique within a segment
        # (test_every_matrix_persona_label_has_a_unique_key_within_its_segment pins this) --
        # fail closed rather than silently pick one if that invariant is ever violated.
        return RowCell(
            email,
            persona_key,
            norm_segment,
            clean_signal,
            None,
            reason="ambiguous: more than one matrix cell matches this persona x signal x segment",
        )
    return RowCell(email, persona_key, norm_segment, clean_signal, candidates[0])


def classify_rows(matrix: Matrix, rows: list[dict]) -> list[RowCell]:
    """:func:`derive_row_cell` over a CSV's live (non-suppressed) rows."""
    out: list[RowCell] = []
    for r in rows:
        if (r.get("suppression") or "").strip():
            continue
        out.append(
            derive_row_cell(
                matrix,
                email=(r.get("email") or "?").strip(),
                title=r.get("title") or "",
                segment=r.get("segment") or "",
                signal_column=r.get(SIGNAL_COLUMN) or "",
            )
        )
    return out


def audit_campaign(
    profile: str,
    campaign: str = "",
    *,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
    min_recipients: int = MIN_RECIPIENTS,
    min_arguments: int = MIN_ARGUMENTS,
    min_segment_fit: float = MIN_SEGMENT_FIT,
    min_signal_attestation: float = MIN_SIGNAL_ATTESTATION,
    include_drafts: bool = False,
) -> Coverage:
    """Measure one campaign's message axis, from ``cells.toml`` outward.

    ``cells.toml`` is the row set, never a glob over the sequences directory. The
    directory holds supersets that were split and replaced, so a glob double-counts —
    and mis-pairing a list with a spec is the exact error ``cells.toml``'s own header
    exists to prevent.

    ``include_drafts`` additionally folds in every complete cell under ``evals/drafts/``.
    Those are deliberately not in ``cells.toml``, so without the flag a drafted pilot is
    invisible here — off by default because a drafted cell has no outcomes and must never
    silently widen a report about live copy.
    """
    profiles_root = profiles_root or resolve_profiles_root()
    cov = Coverage(
        campaign=campaign,
        profile=profile,
        min_recipients=min_recipients,
        min_arguments=min_arguments,
        min_segment_fit=min_segment_fit,
        min_signal_attestation=min_signal_attestation,
    )

    cov.matrix = parse_matrix(resolve_knowledge_file(profiles_root, profile, "hook-matrix.md"))
    if not cov.matrix.ok:
        cov.findings.append(f"matrix-unsupported: hook-matrix.md — {cov.matrix.reason}")
    elif cov.matrix.unmapped_personas():
        unmapped = ", ".join(cov.matrix.unmapped_personas()[:EXEMPLARS])
        cov.findings.append(
            f"persona-unmapped: hook-matrix.md — {len(cov.matrix.unmapped_personas())} "
            f"matrix persona(s) do not normalise onto the role vocabulary ({unmapped}); "
            f"recipients can never be attributed to them"
        )

    cap_vocab = capability_vocab(profile, profiles_root)

    seq_dir = _prospects_dir(profile, content_root) / "sequences"
    sources = [
        s
        for s in load_cell_map(profile, content_root)
        if not campaign or s.get("campaign") == campaign
    ]
    # A ``cells.toml`` entry names its files relative to the sequences dir; a drafted cell
    # is found by walking. Both are resolved to absolute paths HERE, once, so the loop below
    # has one kind of source rather than two — and so ``cov.sources`` keeps exactly the shape
    # cells.toml gives it, with no resolution key smuggled into it for the loop's benefit.
    resolved: list[tuple[dict, Path, Path]] = [
        (s, seq_dir / s["spec"], seq_dir / s["csv"]) for s in sources
    ]
    # Drafted cells are absent from cells.toml by design (they have no outcomes to
    # attribute), so an audit that read only that file was structurally blind to a pilot —
    # exactly the artifact `argument-monotone` exists to judge. Folded in behind a flag, and
    # tagged in `cov.drafts`, so a reader is never told a pilot's finding is about live copy.
    if include_drafts:
        for slug, spec_path, csv_path in draft_cell_dirs(profile, content_root):
            key = f"drafts/{slug}/{spec_path.name}"
            cov.drafts.add(key)
            src = {"campaign": campaign or "drafts", "spec": key, "csv": str(csv_path)}
            sources.append(src)
            resolved.append((src, spec_path, csv_path))
    cov.sources = sources

    spec_texts: dict[str, str] = {}
    for src, spec_path, csv_path in resolved:
        if spec_path.is_file():
            text = spec_path.read_text(encoding="utf-8")
            spec_texts[src["spec"]] = text
            try:
                cov.declared[src["spec"]] = declared_cell(text, cov.matrix)
            except UnknownHookCell as exc:
                cov.declared[src["spec"]] = None
                cov.findings.append(f"hook-cell-unknown: {src['spec']} — {exc}")
            try:
                cov.capabilities[src["spec"]] = declared_capability(text, cap_vocab)
            except ValueError as exc:
                cov.capabilities[src["spec"]] = ""
                cov.findings.append(f"capability-unknown: {src['spec']} — {exc}")
        if not csv_path.is_file():
            continue
        # Per-spec, not only campaign-wide: the fit checks below ask whether THIS spec
        # matches THIS list, a question the aggregate counters cannot answer.
        spec_segments: Counter = Counter()
        spec_evidence: list[str] = []
        row_cells: Counter = Counter()
        rows_seen = 0
        with csv_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if (row.get("suppression") or "").strip():
                    continue
                rows_seen += 1
                cov.rows += 1
                title = (row.get("title") or "").strip()
                row_segment = (row.get("segment") or "").strip()
                recorded = (row.get(HOOK_CELL_COLUMN) or "").strip()
                if not recorded and cov.matrix is not None and cov.matrix.ok:
                    # HOOK_CELL_COLUMN is unpopulated on every list measured 2026-08-23
                    # (0/207 live rows). Derive it from the row's own title/segment/signal
                    # rather than leaving `row_cells` empty and `cell-row-mismatch` inert —
                    # this is the fix for the exact defect this module exists to close.
                    rc = derive_row_cell(
                        cov.matrix,
                        email=(row.get("email") or "?").strip(),
                        title=title,
                        segment=row_segment,
                        signal_column=row.get(SIGNAL_COLUMN) or "",
                    )
                    if rc.cell is not None:
                        recorded = f"{rc.cell.persona} × {rc.cell.signal}"
                if recorded:
                    row_cells[recorded] += 1
                cov.seats[seat_of(title) or "unresolved"] += 1
                key = persona_of(title)
                if key:
                    cov.personas[key] += 1
                    if cov.matrix is not None and cov.matrix.ok:
                        norm_seg = _norm_segment(row_segment)
                        in_grid = any(
                            persona_key_of_label(c.persona) == key
                            and _norm_segment(c.segment) == norm_seg
                            for c in cov.matrix.cells.values()
                        )
                        if not in_grid:
                            cov.unassignable[f"{key}/{norm_seg or '(no segment)'}"] += 1
                else:
                    cov.unresolved[title or "(no title)"] += 1
                spec_segments[row_segment] += 1
                spec_evidence.append(
                    " ".join(
                        (row.get(f) or "") for f in ("signal_clause", "signal_evidence", "why_now")
                    )
                )
        declared_for_spec = cov.declared.get(src["spec"])
        sfit = segment_fit(src["spec"], declared_for_spec, cov.matrix, spec_segments)
        if sfit is not None:
            cov.segment_fits[src["spec"]] = sfit
        gfit = signal_fit(src["spec"], declared_for_spec, cov.matrix, spec_evidence)
        if gfit is not None:
            cov.signal_fits[src["spec"]] = gfit
        cov.row_cells[src["spec"]] = (row_cells, rows_seen)

    undeclared = [name for name, d in cov.declared.items() if d is None]
    if undeclared:
        shown = ", ".join(sorted(undeclared)[:EXEMPLARS])
        more = f", +{len(undeclared) - EXEMPLARS} more" if len(undeclared) > EXEMPLARS else ""
        cov.findings.append(
            f"hook-cell-missing: {len(undeclared)} of {cov.specs} spec(s) — "
            f"no hook_cell declared ({shown}{more}); the matrix cell each spec "
            f"implements is unverifiable"
        )

    cov.overlaps = argument_distinctness(spec_texts)
    cov.shared = shared_phrases(spec_texts)
    if cov.shared:
        worst_phrase = cov.shared[0]
        cov.findings.append(
            f"template-share: {len(cov.shared)} phrase(s) — a {NGRAM_N}-word phrase "
            f"appears verbatim in more than {MAX_NGRAM_EMAILS} of {cov.specs} spec(s), "
            f'worst "{worst_phrase.phrase}" in {worst_phrase.count}; '
            f"scaffold and argument are indistinguishable at this scope by count alone"
        )
    same = [p for p in cov.overlaps if p.same_argument]
    if same:
        worst = same[0]
        cov.findings.append(
            f"argument-monoculture: {len(same)} of {len(cov.overlaps)} spec pair(s) — "
            f"overlap above {JACCARD_MAX:.2f}, worst {worst.a} vs {worst.b} at "
            f"{worst.jaccard:.2f}; these are one argument wearing several subjects"
        )

    # `argument-monoculture` above measures the COPY (bag-of-word overlap); this measures
    # what the spec SAYS it argues. They catch different failures: the 2026-08-23 pilot
    # scored as distinguishable prose while making one argument seven times, because
    # different nouns for one claim are still one claim.
    cov.findings.extend(capability_monotone(cov.capabilities))
    undeclared = sorted(s for s, c in cov.capabilities.items() if not c)
    if undeclared and cap_vocab:
        shown = ", ".join(undeclared[:EXEMPLARS])
        more = f" (+{len(undeclared) - EXEMPLARS} more)" if len(undeclared) > EXEMPLARS else ""
        # Advisory, not blocking: `capability:` is new and every spec written before it
        # predates the field, exactly as `premise:` did. An unmigrated spec is not a
        # violation — but an uncounted one weakens the cap, so it is never silent.
        cov.warnings.append(
            f"capability-undeclared: {len(undeclared)} of {cov.specs} spec(s) declare no "
            f"`capability:` ({shown}{more}); they are invisible to the "
            f"{MAX_SPECS_PER_CAPABILITY}-per-group cap until they do"
        )

    if cov.declared and cov.arguments < min_arguments:
        cov.findings.append(
            f"argument-count: {cov.campaign or profile} — {cov.arguments} distinct "
            f"argument(s) across {cov.specs} spec(s) and {cov.rows} recipient(s), "
            f"target >= {min_arguments}"
        )

    for name in sorted(cov.segment_fits):
        fit = cov.segment_fits[name]
        if not fit.failed(min_segment_fit):
            continue
        other = ", ".join(f"{k or '(blank)'} {n}" for k, n in fit.counts.most_common(EXEMPLARS))
        cov.findings.append(
            f"cell-segment-fit: {name} — declared cell sits in the "
            f"{fit.declared_segment!r} grid but only {fit.matched}/{fit.rows} "
            f"({fit.share:.0%}) of recipients are {fit.declared_segment}, below "
            f"{min_segment_fit:.0%}; list is {other}"
        )

    for name in sorted(cov.signal_fits):
        fit = cov.signal_fits[name]
        # A PASSING score carried entirely by list-wide words is more dangerous than a
        # failing one, because nothing else in the report contradicts it. Warned separately
        # and before the threshold check, so a cell nobody can measure is never silently
        # counted as a cell that measured well.
        if fit.noise_dominated:
            common = ", ".join(sorted(fit.noise_terms))
            cov.warnings.append(
                f"cell-signal-unmeasurable: {name} — the declared signal {fit.signal!r} "
                f"yields only list-wide terms ({common}), each matching over "
                f"{SIGNAL_TERM_NOISE_SHARE:.0%} of these recipients. Its "
                f"{fit.share:.0%} attestation is NOT evidence the signal was observed; read "
                f"it as 'this gate cannot see this cell'. Fix the matrix label or research "
                f"the signal — a threshold change would only hide it."
            )
            continue
        if not fit.failed(min_signal_attestation):
            continue
        matched = ", ".join(f"{t} x{n}" for t, n in fit.hits.most_common(EXEMPLARS)) or "none"
        cov.warnings.append(
            f"cell-signal-fit: {name} — {fit.attested}/{fit.rows} ({fit.share:.0%}) of "
            f"recipients carry evidence for the declared signal {fit.signal!r}, below "
            f"{min_signal_attestation:.0%}; distinctive terms "
            f"{'/'.join(fit.terms) or '(none derivable)'}, matched {matched}"
        )

    # `cell-row-mismatch` — the equality test the two fit heuristics above are approximating.
    #
    # `cell-segment-fit` and `cell-signal-fit` both have to INFER the row's side of the
    # comparison, because only the spec declares anything: segment fit reads an enumerated
    # column (strong), signal fit reads free text for distinctive terms (weak, hence WARN).
    # Once `prospect` records the cell it actually picked for the account, there is nothing
    # to infer — the row states its cell and the spec states its cell, and they either match
    # or they do not. That is why this is an ERROR while `cell-signal-fit` is a WARN.
    #
    # Migration: a list written before the column exists reports ONE advisory line naming the
    # spec, never a per-row wall and never a failure. Same shape as the record-columns
    # migration in `signal_record` — a gate that answers "your list is from last week" with N
    # identical errors is the unreadable-output failure `finding_budget` exists to stop.
    for name in sorted(cov.row_cells):
        counts, rows_seen = cov.row_cells[name]
        declared = cov.declared.get(name)
        if declared is None or not rows_seen:
            continue
        if not counts:
            cov.warnings.append(
                f"cell-row-unrecorded: {name} — none of {rows_seen} row(s) record a "
                f"{HOOK_CELL_COLUMN!r}, so which cell each recipient belongs to is still "
                f"inferred rather than known; cell-segment-fit / cell-signal-fit stay "
                f"heuristics on this list. Re-run the prospect skill's research step."
            )
            continue
        matching = sum(n for cell, n in counts.items() if _cells_equal(cell, declared.raw))
        if matching == rows_seen:
            continue
        other = ", ".join(f"{c!r} x{n}" for c, n in counts.most_common(EXEMPLARS))
        cov.findings.append(
            f"cell-row-mismatch: {name} — spec declares {declared.raw!r} but only "
            f"{matching}/{rows_seen} recipients were researched into that cell; "
            f"rows record {other}. The copy argues one cell at a list selected for another."
        )

    cov.findings.extend(
        persona_coverage(cov.personas, cov.declared, cov.matrix, min_recipients=min_recipients)
    )
    return cov


def _cells_equal(a: str, b: str) -> bool:
    """Compare two cell labels tolerantly of the separator and of spacing.

    A recorded cell and a declared cell are written by different producers (the research
    step and the spec author), so ``CISO x MCP`` and ``CISO × MCP`` must compare equal or
    the check fails on punctuation and gets switched off — which is how a real gate becomes
    an ignored one.
    """

    def norm(s: str) -> str:
        s = (s or "").replace("×", " x ").lower()
        return " ".join(s.split())

    return norm(a) == norm(b)


def render(cov: Coverage) -> str:
    m = cov.matrix
    lines = [
        f"hook coverage — {cov.campaign or cov.profile} "
        f"· {cov.specs} spec(s) · {cov.rows} live recipient(s)",
        "",
    ]
    if m is not None:
        if m.ok:
            lines.append(
                f"  matrix: {len(m.cells)} cell(s) · shape {m.shape} · "
                f"{len(m.personas)} persona(s) x {len(m.signals)} signal(s) "
                f"across {len(m.segments)} segment(s)"
            )
        else:
            lines.append(f"  matrix: UNSUPPORTED ({m.shape}) — {m.reason}")
    lines.append(f"  declared cells: {cov.declared_count}/{cov.specs}")
    for name in sorted(cov.declared):
        d = cov.declared[name]
        shown = f"{d.persona} x {d.signal}" if d else "— none declared"
        lines.append(f"    {name[:52]:<54} {shown}")
    if cov.seats:
        lines.append("  seat (copy axis — what persona-lead-mismatch lints against):")
        for k, n in cov.seats.most_common():
            pct = f"{n / cov.rows:.0%}" if cov.rows else "-"
            lines.append(f"    {k:<16} {n:>5}  {pct}")
    if cov.personas or cov.unresolved:
        lines.append("  persona (message axis — what a hook_cell is chosen against):")
        for k, n in cov.personas.most_common():
            pct = f"{n / cov.rows:.0%}" if cov.rows else "-"
            lines.append(f"    {k:<16} {n:>5}  {pct}")
        if cov.unresolved:
            pct = f"{cov.unresolved_rows / cov.rows:.0%}" if cov.rows else "-"
            lines.append(
                f"    {'unresolved':<16} {cov.unresolved_rows:>5}  {pct}  "
                f"({len(cov.unresolved)} distinct title(s))"
            )
            for title, n in cov.unresolved.most_common(EXEMPLARS):
                lines.append(f"      e.g. {title[:60]} ({n})")
        if cov.unassignable:
            n = sum(cov.unassignable.values())
            pct = f"{n / cov.rows:.0%}" if cov.rows else "-"
            lines.append(
                f"    {'wrong-grid':<16} {n:>5}  {pct}  "
                f"(persona known, no cell in this row's own segment)"
            )
            for reason, count in cov.unassignable.most_common(EXEMPLARS):
                lines.append(f"      e.g. {reason} ({count})")
    if cov.unassignable_rows:
        lines.append(
            f"  unassignable to any matrix cell: {cov.unassignable_rows} row(s) — "
            f"{cov.unresolved_rows} unresolved persona + {sum(cov.unassignable.values())} "
            f"persona known but wrong segment grid. This is a list/matrix fact, not a "
            f"copy defect — widening the matrix or resolver is the operator's call."
        )
    if cov.overlaps:
        lines.append(f"  argument overlap (Jaccard, threshold {JACCARD_MAX:.2f}):")
        for p in cov.overlaps:
            flag = "  SAME ARGUMENT" if p.same_argument else ""
            lines.append(f"    {p.jaccard:.2f}  {p.a[:34]:<36} vs {p.b[:34]}{flag}")
    lines.append(
        f"  shared phrases (verbatim {NGRAM_N}-grams in > {MAX_NGRAM_EMAILS} specs): "
        f"{len(cov.shared)}"
    )
    for sp in cov.shared[:EXEMPLARS]:
        lines.append(f'    x{sp.count}  "{sp.phrase}"')
    lines.append(f"  distinct arguments: {cov.arguments} (target >= {cov.min_arguments})")
    if cov.capabilities:
        declared_caps = Counter(c for c in cov.capabilities.values() if c)
        blank = sum(1 for c in cov.capabilities.values() if not c)
        # Printed as a distribution, not only as violations: the number a drafter needs
        # before writing the next spec is "which groups are already taken", and a report
        # that speaks only when the cap breaks cannot answer that.
        lines.append(
            f"  capability spread (cap {MAX_SPECS_PER_CAPABILITY} spec(s) per group): "
            f"{len(declared_caps)} group(s) declared, {blank} spec(s) undeclared"
        )
        for slug, n in declared_caps.most_common():
            flag = "  OVER CAP" if n > MAX_SPECS_PER_CAPABILITY else ""
            lines.append(f"    x{n}  {slug}{flag}")
    if cov.segment_fits or cov.signal_fits:
        lines.append("  list vs declared cell (is the copy aimed at these recipients?):")
        for name in sorted(set(cov.segment_fits) | set(cov.signal_fits)):
            sfit = cov.segment_fits.get(name)
            gfit = cov.signal_fits.get(name)
            seg = f"segment {sfit.share:>4.0%} {sfit.declared_segment}" if sfit else "segment    — "
            sig = f"signal {gfit.share:>4.0%}" if gfit else "signal    — "
            flag = ""
            if sfit and sfit.failed(cov.min_segment_fit):
                flag = "  MISAIMED"
            elif gfit and gfit.failed(cov.min_signal_attestation):
                flag = "  unattested"
            lines.append(f"    {name[:44]:<46} {seg}  {sig}{flag}")
    lines.append("")
    if cov.findings:
        lines.append(f"  FAIL — {len(cov.findings)} finding(s):")
        lines.extend(f"    - {f}" for f in cov.findings)
    else:
        lines.append("  PASS — no hook-coverage findings.")
    if cov.warnings:
        lines.append(f"  {len(cov.warnings)} warning(s) (advisory, do not fail the run):")
        lines.extend(f"    - {w}" for w in cov.warnings)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="gtm_core.hook_coverage",
        description=(
            "Measure a campaign's message axis: which matrix cells its specs declare, "
            "whether its arguments are distinguishable, and which personas hold "
            "recipients no spec addresses."
        ),
    )
    p.add_argument("--profile", required=True, help="active profile (tenant)")
    p.add_argument("--campaign", default="", help="campaign slug from cells.toml (default: all)")
    p.add_argument(
        "--matrix-only",
        action="store_true",
        help="parse and summarise the profile's hook-matrix.md, then stop",
    )
    p.add_argument(
        "--min-recipients",
        type=int,
        default=MIN_RECIPIENTS,
        help=f"recipients a persona needs before 'unaddressed' is a finding "
        f"(default {MIN_RECIPIENTS})",
    )
    p.add_argument(
        "--min-arguments",
        type=int,
        default=MIN_ARGUMENTS,
        help=f"distinct arguments a campaign should carry (default {MIN_ARGUMENTS})",
    )
    p.add_argument(
        "--min-segment-fit",
        type=float,
        default=MIN_SEGMENT_FIT,
        help=f"share of a spec's recipients that must sit in its declared cell's "
        f"segment (default {MIN_SEGMENT_FIT:.2f})",
    )
    p.add_argument(
        "--min-signal-attestation",
        type=float,
        default=MIN_SIGNAL_ATTESTATION,
        help=f"share of recipients whose evidence must attest the declared signal; "
        f"advisory only (default {MIN_SIGNAL_ATTESTATION:.2f})",
    )
    p.add_argument(
        "--include-drafts",
        action="store_true",
        help="also audit drafted cells under prospects/evals/drafts/ (absent from "
        "cells.toml by design, so invisible without this)",
    )
    p.add_argument("--warn-only", action="store_true", help="report findings but exit 0")
    args = p.parse_args(argv)

    if args.matrix_only:
        matrix = parse_matrix(
            resolve_knowledge_file(resolve_profiles_root(), args.profile, "hook-matrix.md")
        )
        if not matrix.ok:
            print(f"{args.profile:<16} UNSUPPORTED ({matrix.shape}) — {matrix.reason}")
            return 0 if args.warn_only else 1
        print(
            f"{args.profile:<16} {matrix.shape:<10} {len(matrix.cells):>4} cell(s) · "
            f"{len(matrix.personas)} persona(s) x {len(matrix.signals)} signal(s) · "
            f"segments: {', '.join(matrix.segments)}"
        )
        for label in matrix.unmapped_personas():
            print(f"  unmapped persona: {label}")
        return 0

    cov = audit_campaign(
        args.profile,
        args.campaign,
        min_recipients=args.min_recipients,
        min_arguments=args.min_arguments,
        min_segment_fit=args.min_segment_fit,
        min_signal_attestation=args.min_signal_attestation,
        include_drafts=args.include_drafts,
    )
    print(render(cov))
    return 0 if (args.warn_only or not cov.failed) else 1


if __name__ == "__main__":
    sys.exit(main())
