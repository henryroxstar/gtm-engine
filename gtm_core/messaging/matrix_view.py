"""``hook-matrix.md`` as a generated view of ``angles.toml`` — the sixth writer for ``profiles/``.

This package holds **two** of the writers ``docs/RULES.md`` enumerates, and they are not the
same shape of guarantee. :mod:`gtm_core.messaging.angle_status` is key-scoped: it moves one
line and re-parses to prove nothing else did. This one replaces the **whole file**, so there is
no key to scope; what bounds it is the banner check below plus the fact that the target is
DERIVED — :func:`render` is a pure function of ``angles.toml``, so an overwrite destroys
nothing a rerun cannot rebuild. Do not read this module as licence to whole-file-replace
anything that is not.

**What this is.** The hook matrix used to be the tenant's hand-kept source of truth: a grid of
persona × signal cells, each holding a sentence somebody wrote. Once the angles are facts
(``angles.toml``), the grid is *derived* — one row per seat, one column per premise ×
opener_kind, and in each cell the angle's one-line summary. Rendering it rather than keeping it
means the matrix and the registry cannot disagree, which is the whole point: two answers to
"which seats have an argument" drift, and the drift is invisible because both keep answering.

**Why the output shape is not ours to choose.** :func:`gtm_core.hook_coverage.matrix.parse_matrix`
already reads three matrix shapes across this repo's profiles, and the coverage report is built
on it. So this module renders into the shape that parser detects as a GRID — its header's first
cell names both axes — and the round trip is a contract, not a coincidence
(``tests/unit/test_messaging_matrix_view.py``).

**Why the header says `Seat` and not `Persona`.** The rows are seats, and the parser joins a
recipient to a row by resolving the recipient's title into the row label's key space. Writing
`Persona` there would not merely mislabel the column for a human: it would tell
:func:`~gtm_core.hook_coverage.matrix.parse_matrix` to bucket recipients with ``persona_of``,
which returns ``None`` for every seat name this file writes — every recipient unassignable, a
coverage report of zero, and no defect anywhere to find it. That header cell IS the axis
declaration (:data:`ROW_AXIS_HEAD`).

**Why a hole renders ``—``.** A seat with no angle keeps its row, and the cell says ``—``. That
pairs with the placeholder rule FR2 added to the parser on 2026-09-24: a ``—`` cell parses back
as *no cell*. Both halves are needed and neither works alone. Omit the row and the hole is
invisible to the human reading the markdown; render the ``—`` without the parser rule and every
hole counts as coverage, so the report that answers "how many seats have an argument" reads
high by exactly the number of holes.

**Why the banner.** ``messaging matrix`` overwrites a file under ``profiles/``, which is
otherwise read-only at runtime (CLAUDE.md). A tenant's hand-authored matrix is months of
judgement; a generated one costs a command to rebuild. Line 1 is the only thing that tells them
apart, so a target without it is refused — never merged, never backed up and replaced, just
refused, because the operator is the one who knows which of the two that file is.

Stdlib only. No socket, no paid call, and no write anywhere but the resolved matrix path.
"""

from __future__ import annotations

from pathlib import Path

from ..fsio import atomic_write_text
from ..paths import resolve_knowledge_file
from .registry import ANGLES_FILE, OPENER_KINDS, Angle, Registry, RegistryError

#: The generated file, by the name every skill and the coverage report already know it by.
MATRIX_FILE = "hook-matrix.md"

#: The stable half of the banner. :func:`is_generated` tests for THIS, not for the whole
#: banner: the prose after it is free to improve, and a banner edit that made every existing
#: generated file un-regenerable would be a refusal nobody could clear without hand-editing
#: the very file this module refuses to let anyone hand-edit.
BANNER_MARK = "<!-- gtm_core.messaging:generated"

#: Line 1 of every generated matrix. Deliberately carries **no date and no counts**: a banner
#: that changed on every run would make the drift check below fire on noise, and an operator
#: who learns that ``check`` cries wolf is an operator who stops reading it.
BANNER = f"{BANNER_MARK} — do not edit; regenerate from angles.toml -->"

#: The section an angle with no declared ``segments`` lands in. A heading rather than an empty
#: one because the parser reads an empty heading as the literal segment ``default``, and a
#: tenant that later declares a real segment called "default" would silently merge with it.
UNSEGMENTED = "unsegmented"

#: What the target file is, relative to what the registry says it should be. Reported by
#: ``check``; only :data:`STALE` is a defect. ``HAND_AUTHORED`` is a tenant that has not
#: migrated, which is a fact about the tenant and not a failure of theirs.
ABSENT = "absent"
HAND_AUTHORED = "hand-authored"
CURRENT = "current"
STALE = "stale"

#: What a write did. Two values, because "regenerated and nothing moved" and "regenerated and
#: the grid changed" are different answers to the operator's actual question.
WRITTEN = "written"
UNCHANGED = "unchanged"

#: The word in header cell 0 that tells every reader of the generated file which key space
#: its ROW LABELS live in. This is the axis declaration, and it is deliberately the cell
#: ``gtm_core.hook_coverage.matrix._detect`` already reads to decide the shape — one
#: machine-readable token in the structure, rather than prose in the preamble for a parser to
#: infer, and rather than a second channel in the banner that a hand-authored seat matrix
#: could never carry. It is not IMPORTED from
#: :class:`~gtm_core.hook_coverage.matrix.RowAxis` — that import pulls the dev-only
#: ``tests/linter`` tree onto ``sys.path`` at module load, which the registry may not do (see
#: :func:`_canonical`) — so the agreement between the word written here and the axis the
#: parser reads back is asserted instead, on every render, by
#: ``tests/unit/test_messaging_matrix_view.py``. See :class:`RowAxis` for what a mixed axis
#: costs and why guessing is refused.
ROW_AXIS_HEAD = "Seat"

#: The one escape. A markdown table cell ends at a raw ``|`` and the parser splits on it
#: without honouring a backslash escape, so ``\\|`` would still cut the cell in two. The HTML
#: entity renders as a pipe for a human and survives the parser as text.
_PIPE = "&#124;"


class MatrixError(RegistryError):
    """A registry that cannot be rendered, or a target that must not be overwritten.

    A subclass of :class:`RegistryError` on purpose: to the operator this is the same kind of
    news as a bad ``claims.toml`` — one line naming the file and the id, and a fix they make by
    editing TOML or by moving a file. The CLI renders it through the same path.
    """


def _canonical(text: str) -> str:
    """``text`` as the parser will read it back out of a cell.

    Uses the parser's OWN normaliser rather than a copy of it, because a second definition of
    "what a cell says" is exactly the drift this module exists to remove. Imported inside the
    function for the reason :func:`gtm_core.messaging.registry._load_premises` gives:
    ``hook_coverage.config`` puts ``tests/linter`` on ``sys.path`` at import time, and neither
    the registry nor its CLI may depend on a dev-only tree that a deployed install lacks.

    Applied to a fixpoint, not once. ``_clean_cell`` strips a single leading NEW badge, so two
    of them survive one pass; it never lengthens its input, so the loop terminates.
    """
    from ..hook_coverage.matrix import _clean_cell

    prev = None
    while prev != text:
        prev, text = text, _clean_cell(text)
    return text


def cell_text(summary: str) -> str:
    """One angle's summary as a table cell: pipes escaped, then canonicalised."""
    return _canonical(summary.replace("|", _PIPE))


def rendered_angles(registry: Registry) -> tuple[Angle, ...]:
    """The angles the grid offers, in id order.

    ``retired`` is excluded and NOT deleted from ``angles.toml``: a retired angle is evidence
    about what was tried, but the matrix answers "what is on offer", and an offer that was
    withdrawn is not one.
    """
    return tuple(a for _, a in sorted(registry.angles.items()) if a.status != "retired")


def _label(value: str, what: str, owner: str) -> str:
    """A row, column or section label — refused if the parser would read it as nothing.

    A row whose label canonicalises to empty is skipped wholesale by ``_parse_grid_section``,
    taking every cell on it. That is a silent drop with no defect anywhere downstream: the
    angles do not appear as holes, they do not appear at all, and the count is simply lower.
    """
    out = _canonical(value)
    if not out:
        raise MatrixError(f"{MATRIX_FILE}: {owner} — {what} `{value}` reads as an empty label")
    return out


def _signal(premise: str, opener_kind: str) -> str:
    return f"{premise} × {opener_kind}"


def _grid(registry: Registry) -> tuple[list[str], list[str], list[str], dict[tuple, str]]:
    """``(segments, rows, columns, cells)`` — every axis sorted, so a render is a function.

    The two axes are asymmetric on purpose. ``opener_kind`` is a closed set this module can
    name in full, so both halves are always columns and a missing one shows as a hole.
    ``premise`` is not: the premise vocabulary lives in ``premise-vocab.toml``, which the
    registry reads only to validate against, so the columns are the premises the angles
    actually name. A premise nobody argues is invisible here rather than wrong here.
    """
    from ..hook_coverage.matrix import _is_placeholder

    angles = rendered_angles(registry)

    segments: set[str] = set()
    rows: set[str] = {_label(s, "seat", "role-vocabulary.toml") for s in registry.seats}
    premises: set[str] = set()
    cells: dict[tuple[str, str, str], str] = {}
    owner: dict[tuple[str, str, str], str] = {}

    for angle in angles:
        seat = _label(angle.seat, "seat", angle.id)
        premise = _label(angle.premise, "premise", angle.id)
        signal = _label(_signal(premise, angle.opener_kind), "column", angle.id)
        text = cell_text(angle.summary)
        # A summary the parser reads as a placeholder renders a cell indistinguishable from
        # "no angle here": the grid would show a hole where an angle exists, and the coverage
        # report would agree with the hole. Refused, named by id.
        if _is_placeholder(text):
            raise MatrixError(
                f"{MATRIX_FILE}: {angle.id} — `summary` reads as a blank cell (`{angle.summary}`)"
            )
        # Rows are the UNION of the seat table and the angles' own seats. Taking them from the
        # seat table alone would drop an angle whose seat the table has forgotten — and an
        # angle nobody can see has no defect of its own.
        rows.add(seat)
        premises.add(premise)
        for segment in angle.segments or (UNSEGMENTED,):
            key = (_label(segment, "segment", angle.id), seat, signal)
            if key in cells:
                first, second = sorted((owner[key], angle.id))
                raise MatrixError(
                    f"{MATRIX_FILE}: {first} — shares a cell with {second} "
                    f"(`{key[0]}` · `{seat}` · `{signal}`); one cell holds one summary"
                )
            segments.add(key[0])
            cells[key] = text
            owner[key] = angle.id

    columns = [
        _signal(premise, opener) for premise in sorted(premises) for opener in sorted(OPENER_KINDS)
    ]
    return sorted(segments) or [UNSEGMENTED], sorted(rows), columns, cells


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def render(registry: Registry) -> str:
    """The whole ``hook-matrix.md`` for ``registry``, banner first.

    Deterministic: every axis is sorted, and nothing in the output is a clock, a path or a
    count of the run. Two renders of the same facts are byte-identical, which is what makes
    the drift check in ``messaging check`` mean something.
    """
    segments, rows, columns, cells = _grid(registry)

    out = [
        BANNER,
        "",
        "# Outreach hook matrix",
        "",
        "> **Generated from `angles.toml`. Do not edit — your edit is lost on the next run.**",
        "> One row per seat, one column per premise × opener kind, one cell per angle summary.",
        "> A `—` is a seat with no angle for that column: a hole kept visible on purpose, and",
        "> read back as no coverage rather than as an argument.",
        "> Regenerate: `uv run python -m gtm_core.messaging matrix --profile <active>`",
    ]
    for segment in segments:
        out += [
            "",
            f"## {segment}",
            "",
            _row([f"Signal → / {ROW_AXIS_HEAD} ↓", *columns]),
            _row(["---"] * (len(columns) + 1)),
        ]
        for seat in rows:
            out.append(_row([seat, *(cells.get((segment, seat, col), "—") for col in columns)]))
    return "\n".join(out) + "\n"


# --- the write --------------------------------------------------------------------------


def matrix_path(
    profiles_root: Path,
    profile: str,
    product: str | None = None,
    overlay: str | None = None,
) -> Path:
    """Where this tenant's matrix lives: **beside the ``angles.toml`` it is the view of.**

    Resolved by asking the one resolver for the SOURCE and taking its directory, rather than
    by asking it for ``hook-matrix.md`` directly. Those two are not the same question, because
    two rungs of that ladder are existence-conditional: ``registry.load`` would resolve
    ``products/<S>/angles.toml`` while this resolved ``knowledge/hook-matrix.md`` — so
    ``messaging matrix --profile P --product S`` rendered the PRODUCT's angles and wrote them
    over the COMPANY-wide matrix, silently, because a file this command generated last week
    still carries the banner. A later company-level run flipped it back, and
    ``check --product S`` reported `current` against the wrong file.

    Both directions of the fix were available and the one NOT taken is worth naming: writing
    ``products/<S>/hook-matrix.md`` unconditionally whenever ``--product`` is given would mint
    a product-level view for a product that shares the company's ``angles.toml`` — a file
    derived from facts it does not own, which then shadows the company matrix for every later
    ``--product S`` read and goes stale the moment the company angles move. The target follows
    the source: a product with its own angles gets its own matrix, and one without gets none.

    The derivation adds no path surface — every segment still passes through
    :func:`gtm_core.paths._safe_segment` inside the resolver, and :data:`MATRIX_FILE` is a
    constant of this module.
    """
    angles = resolve_knowledge_file(
        profiles_root, profile, ANGLES_FILE, product=product, overlay=overlay
    )
    return angles.parent / MATRIX_FILE


def is_generated(text: str) -> bool:
    """True when line 1 carries the banner mark — the only thing that makes a file ours.

    Line 1 and not "anywhere in the file": a hand-authored matrix that quotes the banner while
    explaining why it is hand-kept must not thereby become overwritable.
    """
    return text.split("\n", 1)[0].startswith(BANNER_MARK)


def _read(path: Path) -> str:
    """The target's text, with undecodable bytes replaced rather than raising.

    A file this command cannot decode is certainly not one it wrote, and ``errors="replace"``
    lets that fall out of the banner check instead of needing a branch of its own — the
    replaced text can neither start with the banner nor equal a render.
    """
    return path.read_text(encoding="utf-8", errors="replace")


def matrix_state(registry: Registry, path: Path) -> str:
    """One of :data:`ABSENT` / :data:`HAND_AUTHORED` / :data:`CURRENT` / :data:`STALE`."""
    if not path.is_file():
        return ABSENT
    current = _read(path)
    if not is_generated(current):
        return HAND_AUTHORED
    return CURRENT if current == render(registry) else STALE


def write_matrix(registry: Registry, path: Path) -> str:
    """Regenerate ``path`` from ``registry``; refuse a file this command did not write.

    Rendered before the target is touched, so a registry that cannot be rendered leaves the
    existing matrix exactly as it was rather than truncated. Written through
    :func:`gtm_core.fsio.atomic_write_text` — the same guarantee every other rewrite in this
    repo gets — so a reader sees the whole old file or the whole new one and never a half.
    """
    text = render(registry)
    if path.is_file():
        current = _read(path)
        if not is_generated(current):
            raise MatrixError(
                f"{MATRIX_FILE}: {path} — no `{BANNER_MARK}` banner on line 1, so this command "
                "did not write it; refusing to overwrite a hand-authored matrix"
            )
        if current == text:
            return UNCHANGED
    atomic_write_text(path, text)
    return WRITTEN
