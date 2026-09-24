from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import persona_of, seat_of


class RowAxis:
    """Which key space a matrix's ROW LABELS live in — and therefore which resolver joins
    a recipient to a row.

    A matrix row is only useful because a recipient can be put on it: ``hook_coverage``
    answers "how many recipients have an argument" by bucketing recipients with a title
    resolver and matching the bucket against a row label. Until 2026-09-24 there was one
    key space, ``persona``, and the join was hardcoded to :func:`persona_of`.

    FR2 made ``hook-matrix.md`` a generated view of ``angles.toml`` whose rows are **seats**
    (``gtm_core/messaging/matrix_view.py``). A seat is a coarser key: three Enterprise
    personas read the one ``security`` seat, and two personas resolve to no seat at all.
    ``persona_of("security")`` is ``None``, so a seat-row matrix read as persona rows books
    every recipient as unassignable — the exact failure
    :mod:`gtm_core.role_vocabulary`'s docstring describes ("the tenant reads a coverage
    report showing zero and concludes the copy is bad").

    **The two key spaces are never mixed.** A ``persona`` key and a ``seat`` key can be the
    same string (``ceo``, ``cto``) and mean different sets, so a report that resolved row
    labels in one space and recipients in the other would look right and count wrong — with
    no defect anywhere to find it. That is worse than today's loud failure, which is why
    :meth:`Matrix.row_key` and :meth:`Matrix.recipient_key` are a *pair* on the matrix: a
    caller cannot reach one without the axis that chose the other, and a file naming both
    axes is refused (:data:`MatrixShape.UNSUPPORTED`) rather than resolved to a guess.

    Hand-kept tenant matrices keep the ``persona`` axis and behave exactly as before; the
    axis is read out of the file, never assumed from the tenant or from the generator.
    """

    #: Rows are personas — :func:`persona_of` buckets recipients onto them. The default,
    #: because every matrix in this repo before 2026-09-24 was one and a file that names no
    #: axis word at all (the ``sections`` shape) carries persona headings.
    PERSONA = "persona"
    #: Rows are seats — :func:`seat_of` buckets recipients onto them.
    SEAT = "seat"


#: The axis words, as they appear in the header cell / column head the parser already reads
#: for shape detection. Declaring the axis *there* rather than in a generator-specific banner
#: keeps one detection instead of two, works for a hand-authored seat matrix as well as a
#: generated one, and avoids ``hook_coverage`` importing from ``gtm_core.messaging`` — which
#: imports from here, so the dependency only runs one way.
_AXIS_WORDS = (RowAxis.PERSONA, RowAxis.SEAT)


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


def persona_key_of_label(label: str, profile: str | None = None) -> str | None:
    """Normalise a *matrix* persona label onto the same key space as :func:`persona_of`.

    Running one cue list over both a recipient's title and the matrix's own row label
    is what lets the two axes be joined without this module hardcoding any tenant's
    persona names. A label that does not normalise is reported as unmapped, never
    dropped — an unjoinable matrix row is a real gap in the report, not an absence.
    """
    return persona_of(_clean_cell(label), profile)


def seat_key_of_label(label: str, profile: str | None = None) -> str | None:
    """Normalise a *matrix* seat label onto the same key space as :func:`seat_of`.

    Two ways a row label names a seat, tried in that order:

    1. **It is the seat's own name.** A generated matrix writes ``registry.seats`` and
       ``angle.seat`` verbatim, both already validated against ``role-vocabulary.toml``, so
       the label IS the key. This rung is needed and not redundant: ``seat_of`` reads a
       *person's title*, and ``seat_of("security")`` is ``None`` — the cue lists spell that
       seat as ``CISO``/``Head of Security``, never as the seat name.
    2. **It is a title or persona label** — a hand-authored seat matrix, or a row someone
       wrote as ``CISO``. :func:`seat_of` answers that, and it is
       ``persona_of`` + :attr:`~gtm_core.role_vocabulary.RoleVocabulary.persona_to_seat`,
       so this adds no second persona→seat mapping.

    A label that is neither is reported as unmapped, never dropped — same contract as
    :func:`persona_key_of_label`.
    """
    from .. import role_vocabulary

    cleaned = _clean_cell(label)
    if not cleaned:
        return None
    by_name = {s.lower(): s for s in role_vocabulary.load(profile).seats}
    return by_name.get(cleaned.lower()) or seat_of(cleaned, profile)


def row_key_of_label(label: str, axis: str, profile: str | None = None) -> str | None:
    """A matrix ROW LABEL normalised onto ``axis``'s key space, or ``None``."""
    if axis == RowAxis.SEAT:
        return seat_key_of_label(label, profile)
    return persona_key_of_label(label, profile)


def row_key_of_title(title: str, axis: str, profile: str | None = None) -> str | None:
    """A RECIPIENT's title normalised onto ``axis``'s key space, or ``None``.

    The other half of :func:`row_key_of_label`. Both must be called with the *same* axis or
    the join silently compares two key spaces — see :class:`RowAxis`.
    """
    if axis == RowAxis.SEAT:
        return seat_of(title, profile)
    return persona_of(title, profile)


# --- matrix parsing -----------------------------------------------------------------

_TABLE_ROW_RE = re.compile(r"^\s*\|(?P<body>.*)\|\s*$")
_SEPARATOR_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_HEADING_RE = re.compile(r"^(?P<hashes>#{2,3})\s+(?P<title>.+?)\s*$")
_BULLET_RE = re.compile(
    r"^\s*[-*]\s+\*\*(?P<label>[^*]+?)\s*:?\s*\*\*\s*:?\s*(?P<hook>.*)$",
)
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


#: Cell contents that mean "no hook here", not "a hook that reads —".
#
# Measured on 2026-09-24 against this parser: an em-dash cell was recorded as a ``Cell`` with
# ``hook='—'``, counted in ``len(matrix.cells)``, and accepted by ``declared_cell`` as a known
# coordinate. The 2026-09-24 PRD had assumed the opposite and flagged it as the assumption to
# knock down before ``hook-matrix.md`` became a generated view; it did not survive the test.
#
# It has to be handled here rather than at the render step, because a generated matrix renders
# a missing angle as ``—`` *deliberately* — a hole must stay visible as a row, not vanish. With
# no placeholder rule, every hole in the grid parses back as coverage and the report that
# answers "how many seats have an argument" reads high by exactly the number of holes.
#
# An empty cell already behaved correctly (falsy after cleaning); these are the written-out
# forms of the same intent. Matched case-insensitively on the cleaned cell, so a hook is only
# ever discarded when the cell says nothing else at all.
_PLACEHOLDER_CELLS = frozenset({"—", "–", "-", "--", "n/a", "na", "tbd", "?"})


def _is_placeholder(text: str) -> bool:
    """True when a cleaned cell carries no hook — blank, or one of the written-out blanks."""
    return not text or text.lower() in _PLACEHOLDER_CELLS


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
    #: The profile whose role vocabulary resolves this matrix's persona labels. ``None``
    #: falls back to the session's bound profile, then to the shipped default — so a caller
    #: that never learned about tenant vocabularies keeps working, and one that did gets the
    #: tenant's own personas instead of being told they map to nothing.
    profile: str | None = None
    #: Which key space :attr:`Cell.persona` labels live in — see :class:`RowAxis`. Read out
    #: of the file's own header, defaulting to ``persona`` so every hand-kept matrix in this
    #: repo behaves exactly as it did before 2026-09-24.
    row_axis: str = RowAxis.PERSONA

    @property
    def ok(self) -> bool:
        return self.shape != MatrixShape.UNSUPPORTED

    def row_key(self, label: str) -> str | None:
        """A ROW LABEL of this matrix, on this matrix's axis. Pairs with
        :meth:`recipient_key` — see :class:`RowAxis` for why they are never used apart."""
        return row_key_of_label(label, self.row_axis, self.profile)

    def recipient_key(self, title: str) -> str | None:
        """A RECIPIENT's title, on this matrix's axis. Pairs with :meth:`row_key`."""
        return row_key_of_title(title, self.row_axis, self.profile)

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
        """``{row label -> canonical key}`` for every label that normalises on this axis."""
        out = {}
        for label in self.personas:
            key = self.row_key(label)
            if key:
                out[label] = key
        return out

    def unmapped_personas(self) -> tuple[str, ...]:
        """Row labels that normalise onto NOTHING in this matrix's own key space.

        Kept under its original name (and under the ``persona-unmapped`` finding id every
        caller and report already uses) because the question is unchanged — "can a recipient
        ever land on this row" — and only the resolver that answers it moved. A seat row is
        unmapped when it is neither a declared seat nor a title that resolves to one, which
        is exactly as loud a gap as an unmappable persona row was.
        """
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
    """A row-label x signal grid: row label is the persona or seat, column head the signal.

    Which of the two the label is comes from :func:`_detect` and is carried on
    :attr:`Matrix.row_axis`; the parse itself is identical either way, because the label is
    stored verbatim and only ever normalised at join time.
    """
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
            if signal and not _is_placeholder(hook_text):
                cells.append(Cell(segment or "default", persona, signal, hook_text))
    return cells


def _column_index(header: list[str], *needles: str) -> int | None:
    for i, cell in enumerate(header):
        low = _clean_cell(cell).lower()
        if any(n in low for n in needles):
            return i
    return None


def _parse_rows_section(segment: str, body: str, axis: str = RowAxis.PERSONA) -> list[Cell]:
    """One table row per cell, with separate Persona (or Seat) / Signal / hook columns.

    ``axis`` names the column that carries the row label, so the header word that decided
    the axis in :func:`_detect` is the same word read here. Looking for both words would
    let a file whose axis was decided by one column be parsed off the other.
    """
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
                "persona": _column_index(row, axis),
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
        # Same placeholder rule as the grid shape. The three shapes are parsed separately, so a
        # rule applied to one of them is a rule the other two disagree with.
        if persona and signal and not _is_placeholder(hook):
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
            if not _is_placeholder(hook_text):
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


#: Both axis words in one header. Refused rather than resolved: the file has told us two
#: incompatible things about what its rows are, and either answer books every recipient of
#: the other key space wrongly with nothing downstream to notice (:class:`RowAxis`).
_AMBIGUOUS_AXIS = (
    "this matrix's header names BOTH a `persona` axis and a `seat` axis, so which key space "
    "its rows live in is undecidable — a recipient joined on the wrong one is counted "
    "confidently and wrongly. Name exactly one (`Signal → / Seat ↓` or `Signal → / Persona ↓`)"
)


def _axis_of(words: tuple[str, ...]) -> str | None:
    """The single axis these header words name, or ``None`` when zero or both appear."""
    found = [w for w in _AXIS_WORDS if w in words]
    return found[0] if len(found) == 1 else None


def _detect(text: str) -> tuple[str, str, str]:
    """``(shape, row_axis, reason)`` — ``reason`` non-empty only for ``UNSUPPORTED``.

    Shape and axis are decided by the SAME header cell / column head, in one pass, so a file
    cannot be read as a grid on one axis word and parsed on the other.
    """
    for _heading, body in _sections(text):
        for line in body.splitlines():
            row = _split_row(line)
            if row is None:
                continue
            first = _clean_cell(row[0]).lower()
            if "signal" in first:
                present = tuple(w for w in _AXIS_WORDS if w in first)
                if present:
                    axis = _axis_of(present)
                    if axis is None:
                        return MatrixShape.UNSUPPORTED, RowAxis.PERSONA, _AMBIGUOUS_AXIS
                    return MatrixShape.GRID, axis, ""
            has_axis = tuple(w for w in _AXIS_WORDS if _column_index(row, w) is not None)
            has_signal = _column_index(row, "signal") is not None
            if has_axis and has_signal:
                axis = _axis_of(has_axis)
                if axis is None:
                    return MatrixShape.UNSUPPORTED, RowAxis.PERSONA, _AMBIGUOUS_AXIS
                return MatrixShape.ROWS, axis, ""
            break  # only the first table row of a section is a header
    for heading, body in _sections(text):
        if heading and _join_bullets(body):
            # Section headings carry no axis word to read, and every `sections` matrix in
            # this repo heads its sections with a persona. A seat-axis `sections` matrix
            # would need a way to say so before it could be read as one.
            return MatrixShape.SECTIONS, RowAxis.PERSONA, ""
    return (
        MatrixShape.UNSUPPORTED,
        RowAxis.PERSONA,
        "no persona axis and no signal axis in this file — a hook bank of "
        "angles cannot name a persona x signal cell, so hook coverage is not "
        "measurable against it (add Persona and Signal columns to gate it)",
    )


def parse_matrix(source: str | Path, profile: str | None = None) -> Matrix:
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

    shape, axis, reason = _detect(text)
    if shape == MatrixShape.UNSUPPORTED:
        return Matrix(shape=shape, reason=reason, path=path, profile=profile, row_axis=axis)

    cells: list[Cell] = []
    if shape == MatrixShape.SECTIONS:
        cells = _parse_sections_shape(text)
    elif shape == MatrixShape.GRID:
        for heading, body in _sections(text):
            cells.extend(_parse_grid_section(heading, body))
    else:
        for heading, body in _sections(text):
            cells.extend(_parse_rows_section(heading, body, axis))

    return Matrix(
        shape=shape,
        cells={c.key: c for c in cells},
        path=path,
        profile=profile,
        row_axis=axis,
    )


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
