from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import persona_of


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
