"""Every matrix cell no spec declares — the hooks the tenant wrote and nobody is using.

The matrix already held a good argument nobody was arguing, and no report said so. Coverage
is measured per PERSONA (``persona_coverage``, gated on :data:`MIN_RECIPIENTS`), so a cell
whose persona is addressed *by a different argument* and holds plenty of recipients reads as
green while the cell itself is untouched. Measured 2026-09-23: one such cell went unused for
three weeks with every existing report clean — its persona was well over the threshold and
argued from a neighbouring cell the whole time, which is precisely why nothing fired.

**Two rules, both derived from that failure.**

*Never gated on recipient volume.* :data:`MIN_RECIPIENTS` is what made the defect invisible:
the persona held far more than the threshold, which is precisely why the persona-level check
was satisfied. A volume gate here would reproduce the blind spot rather than close it, so
this module takes no ``--min-recipients`` and has no threshold of its own.

*Print the argument, not the coordinates.* A bare ``(segment, persona, signal)`` triple is a
finding nobody acts on — the lesson from 388 unreadable warnings. Each entry carries the
cell's first sentence so a drafter can judge it without opening the matrix.

It is a REPORT and never a refusal (exit 0 either way). A backlog that can fail a run becomes
a reason to stop running it.
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..cells import cells_map_path, load_cell_map
from ..paths import resolve_knowledge_file, resolve_profiles_root
from .config import draft_cell_dirs
from .declared import declared_cell, load_registry_or_reason, resolve_declared_cell
from .matrix import Matrix, UnknownHookCell, parse_matrix


class BacklogUnreadable(ValueError):
    """An input this report cannot fully read.

    Refuse, never skip. A backlog computed from a half-read ``cells.toml`` reports live
    cells as unused — confidently wrong, and worse than no report at all, because a drafter
    acts on it by writing a duplicate argument.
    """


@dataclass(frozen=True)
class BacklogCell:
    """One matrix cell nobody declares, with enough of it to judge without opening the file."""

    segment: str
    persona: str
    signal: str
    #: The cell's first sentence — the argument, not its coordinates.
    opening: str
    argument_id: str = ""

    @property
    def coordinates(self) -> str:
        return f"{self.segment} × {self.persona} × {self.signal}"


@dataclass
class Backlog:
    matrix_path: Path | None = None
    matrix_shape: str = ""
    cells_total: int = 0
    #: Distinct (persona, signal) pairs some spec declares.
    declared_pairs: int = 0
    specs_read: int = 0
    include_drafts: bool = False
    #: Unused cells whose persona label joins the tenant's persona vocabulary.
    unused: list[BacklogCell] = field(default_factory=list)
    #: Unused cells whose persona label does NOT resolve. Listed separately and named —
    #: the same habit as `unresolved`/`unassignable`, never silently dropped. An unjoinable
    #: matrix row is a real gap in the report rather than an absence.
    unmapped: list[BacklogCell] = field(default_factory=list)

    @property
    def used(self) -> int:
        return self.cells_total - len(self.unused) - len(self.unmapped)


def _opening(hook: str) -> str:
    """The cell's first sentence, trimmed. Empty hook text stays empty rather than becoming
    a placeholder — a cell with no hook written is itself worth seeing in this list."""
    text = " ".join((hook or "").split())
    if not text:
        return ""
    for stop in (". ", "? ", "! "):
        idx = text.find(stop)
        if idx != -1:
            return text[: idx + 1]
    return text


def _norm_pair(persona: str, signal: str) -> tuple[str, str]:
    """The identity a declaration and a cell are compared under.

    The same normalisation the matrix parser uses, via ``_cells_equal``'s rule: separator
    and spacing tolerant, case-folded. A cell must not read as covered under one spelling
    and unused under another — that would make this report a spelling test.
    """

    def norm(value: str) -> str:
        return " ".join((value or "").replace("×", " x ").lower().split())

    return norm(persona), norm(signal)


def _declared_pairs(
    profile: str,
    content_root: Path | None,
    matrix: Matrix,
    *,
    include_drafts: bool,
    registry=None,
) -> tuple[set[tuple[str, str]], int]:
    """``({(persona, signal)}, specs read)`` across every campaign's specs.

    Spans ALL campaigns, not one: a cell argued in last month's campaign is a cell in use,
    and scoping this to one campaign would report it as backlog every time a new campaign
    started.
    """
    path = cells_map_path(profile, content_root)
    if path.is_file():
        # `load_cell_map` is fail-closed and returns [] for a malformed file. Silence is the
        # wrong answer HERE specifically: no sources means every cell reads as unused, which
        # is a maximally wrong report delivered with total confidence.
        try:
            with path.open("rb") as fh:
                tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise BacklogUnreadable(f"{path} could not be read: {exc}") from exc

    # A `cells.toml` entry names its spec RELATIVE to the sequences dir — the same
    # resolution `audit_campaign` does, in one place, so the two reports cannot disagree
    # about which file a spec is.
    seq_dir = path.parent
    specs: list[Path] = [seq_dir / src["spec"] for src in load_cell_map(profile, content_root)]
    if include_drafts:
        specs += [spec for _slug, spec, _csv in draft_cell_dirs(profile, content_root)]

    pairs: set[tuple[str, str]] = set()
    read = 0
    for spec in specs:
        if not spec.is_file():
            continue
        try:
            text = spec.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise BacklogUnreadable(f"{spec} could not be read: {exc}") from exc
        read += 1
        try:
            # Validated against the matrix: a spec declaring a cell the matrix does not
            # define must not silently mark nothing as used. Resolved through the ANGLE
            # where the spec declares one (FR3) — the backlog names cells no spec argues,
            # and a spec written to the shipped template declares no `hook_cell:` at all,
            # so reading only the legacy field would report every migrated spec's cell as
            # backlog and send a drafter to write copy that already exists.
            declared = resolve_declared_cell(text, matrix, registry)
        except UnknownHookCell:
            # Unvalidated LEGACY read, deliberately not a second angle attempt: an unknown
            # angle derives no cell at all, so there is nothing to mark used and retrying
            # the same resolver here would only re-raise.
            declared = declared_cell(text)
        if declared is not None:
            pairs.add(_norm_pair(declared.persona, declared.signal))
    return pairs, read


def backlog(
    profile: str,
    *,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
    include_drafts: bool = False,
    overlay: str | None = None,
    registry=None,
) -> Backlog:
    """Every cell in the tenant's matrix that no spec declares.

    Read-only: parses ``hook-matrix.md`` and the specs ``cells.toml`` names, writes nothing,
    and refuses nothing. ``include_drafts`` folds in the drafted cells under
    ``evals/drafts/`` — without it a cell a pilot is already piloting reports as unused.

    ``registry`` is what lets a spec's ``angle:`` resolve into the cell it argues. Passed in
    for tests; loaded from ``profile`` otherwise, and a registry that will not load leaves
    the legacy ``hook_cell:`` read running with the reason on stderr. Never silent: this
    report's whole output is "nobody argues these cells", so a declaration this could not
    read sends a drafter to write copy that already exists.
    """
    roots = profiles_root or resolve_profiles_root()
    if registry is None:
        registry, reason = load_registry_or_reason(profile, roots, overlay=overlay)
        if reason:
            print(reason, file=sys.stderr)
    path = resolve_knowledge_file(roots, profile, "hook-matrix.md", overlay=overlay)
    matrix = parse_matrix(path, profile=profile)
    out = Backlog(
        matrix_path=path,
        matrix_shape=matrix.shape,
        include_drafts=include_drafts,
    )
    if not matrix.ok:
        raise BacklogUnreadable(f"{path}: {matrix.reason}")

    out.cells_total = len(matrix.cells)
    pairs, out.specs_read = _declared_pairs(
        profile, content_root, matrix, include_drafts=include_drafts, registry=registry
    )
    out.declared_pairs = len(pairs)

    for cell in matrix.cells.values():
        if _norm_pair(cell.persona, cell.signal) in pairs:
            continue
        entry = BacklogCell(
            segment=cell.segment,
            persona=cell.persona,
            signal=cell.signal,
            opening=_opening(cell.hook),
            argument_id=cell.argument_id,
        )
        if matrix.row_key(cell.persona) is None:
            out.unmapped.append(entry)
        else:
            out.unused.append(entry)

    key = lambda c: (c.segment.lower(), c.persona.lower(), c.signal.lower())  # noqa: E731
    out.unused.sort(key=key)
    out.unmapped.sort(key=key)
    return out


def render_backlog(b: Backlog) -> str:
    """The report. One readable block, never a stream (a backlog that pages you is muted)."""
    lines = [
        f"HOOK BACKLOG — {b.used}/{b.cells_total} cell(s) argued by "
        f"{b.specs_read} spec(s)" + ("  [drafts included]" if b.include_drafts else ""),
        "=" * 78,
    ]
    if not b.unused and not b.unmapped:
        lines.append("  every cell in the matrix is declared by some spec.")
        return "\n".join(lines)

    segment = None
    for cell in b.unused:
        if cell.segment != segment:
            segment = cell.segment
            lines.append(f"\n{segment or '(no segment)'}")
        lines.append(f"  {cell.persona} × {cell.signal}")
        if cell.opening:
            lines.append(f"      {cell.opening}")

    if b.unmapped:
        lines.append(
            f"\nUNMAPPED PERSONA — {len(b.unmapped)} unused cell(s) whose persona label does "
            f"not join the tenant's vocabulary. Named rather than dropped: these are "
            f"unreachable by any recipient join until the label or the cue list is fixed."
        )
        for cell in b.unmapped:
            lines.append(f"  {cell.coordinates}")
    return "\n".join(lines)
