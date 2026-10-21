"""The email text for a person: a sequence's spec filled in with their own list row, or a pack as written.

This is the same filling the status page's "actual emails" section does, with the SAME renderer
(``outreach.render``) — not a second one. What it adds is a detector, because the renderer has no
failure signal: it returns ``""`` for a mapped tag whose column is empty ("Hi ,") and leaves an
unmapped ``{{Tag}}`` in the text, and neither raises. A cell the operator might copy must not
carry either, so a touch that would is written as ``UNAVAILABLE: <reason>`` instead.

What a cell holds is "the spec, filled in with the list as it stands now" — NOT a record of what
was sent. An edit made in the Saleshandy screen and never written down is invisible here.

Each spec is parsed once and cached; the cache lives for one build.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

#: The log shows at most this many touches per person. A longer spec is refused, not truncated.
MAX_TOUCHES = 3
SAME_THREAD = "(reply in the same thread)"
UNAVAILABLE = "UNAVAILABLE: "
#: Written when the person is known to be in a sequence but is on no registered list, so there is
#: no row to fill the spec's merge fields from. An information gap, not a defect in the spec.
NO_LIST_ROW = (
    UNAVAILABLE + "this person is on no registered list, so the merge fields cannot be filled"
)

COLUMNS = tuple(
    f"touch_{n}_{part}" for n in range(1, MAX_TOUCHES + 1) for part in ("subject", "body")
)


def _linter_on_path() -> None:
    """Put ``tests/linter`` (the ``outreach`` package) on ``sys.path``.

    The renderer and the spec parser live there on purpose (``cells.py`` and
    ``hook_coverage/config.py`` take the same import for the same reason: two definitions of
    one rule would drift). It is done explicitly here rather than by importing one of those
    modules first, because an import-sorting formatter is free to reorder that trick away.
    """
    linter = Path(__file__).resolve().parents[1] / "tests" / "linter"
    if str(linter) not in sys.path:
        sys.path.insert(0, str(linter))


def _renderer():
    """``(parse_spec, render, TAG_TO_COLUMN, merge-tag regex)`` — function-local so neither
    this module nor its importers pay for the import up front."""
    _linter_on_path()
    from outreach import _MERGE_TAG_RE, TAG_TO_COLUMN, parse_spec, render

    return parse_spec, render, TAG_TO_COLUMN, _MERGE_TAG_RE


@dataclass(frozen=True)
class SpecResult:
    touches: tuple
    problem: str  # "" when the spec is usable


class SpecCache:
    """Parse each spec file once per build."""

    def __init__(self, seq_dir: Path):
        self._dir = seq_dir
        self._done: dict[str, SpecResult] = {}

    def get(self, spec_name: str) -> SpecResult:
        if spec_name not in self._done:
            self._done[spec_name] = self._load(spec_name)
        return self._done[spec_name]

    def _load(self, spec_name: str) -> SpecResult:
        path = self._dir / spec_name
        if not path.is_file():
            return SpecResult((), f"spec file {spec_name} is missing")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return SpecResult((), f"spec file {spec_name} cannot be read")
        parse_spec = _renderer()[0]
        try:
            touches = tuple(parse_spec(text))
        except Exception:  # noqa: BLE001 - a malformed spec must not take the build down
            return SpecResult((), f"spec file {spec_name} cannot be parsed")
        if not touches:
            return SpecResult((), f"spec file {spec_name} has no email steps")
        if len(touches) > MAX_TOUCHES:
            return SpecResult(
                (),
                f"spec file {spec_name} has {len(touches)} steps and the log shows {MAX_TOUCHES}",
            )
        return SpecResult(touches, "")


def blank() -> dict[str, str]:
    return dict.fromkeys(COLUMNS, "")


def sequence_cells(spec: SpecResult, row: dict) -> tuple[dict[str, str], list[str]]:
    """``(the touch columns, the problems found)`` for one person's list row."""
    cells = blank()
    if spec.problem:
        cells["touch_1_body"] = UNAVAILABLE + spec.problem
        return cells, [spec.problem]
    _parse, render, tag_to_col, tag_re = _renderer()
    problems: list[str] = []
    for n, touch in enumerate(spec.touches, 1):
        subject_t = getattr(touch, "subject", "") or ""
        body_t = getattr(touch, "body", "") or ""
        bad = ""
        for tag in tag_re.findall(subject_t + "\n" + body_t):
            col = tag_to_col.get(tag.strip())
            if col is None:
                bad = f"merge-field {tag.strip()} is not one the renderer knows"
            elif not str(row.get(col) or "").strip():
                bad = f"merge-field {tag.strip()} is empty for this person"
            if bad:
                break
        if bad:
            cells[f"touch_{n}_subject"] = cells[f"touch_{n}_body"] = UNAVAILABLE + bad
            problems.append(bad)
            continue
        cells[f"touch_{n}_subject"] = render(subject_t, row) if subject_t else SAME_THREAD
        cells[f"touch_{n}_body"] = render(body_t, row)
    return cells, problems


def pack_cells(path: Path) -> dict[str, str]:
    """Touch 1 of a 1:1 pack, verbatim — it is already one named person with no merge fields.
    A pack with no email section (LinkedIn only) has no touch to show, which is not a problem."""
    cells = blank()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        cells["touch_1_body"] = UNAVAILABLE + "the pack file cannot be read"
        return cells
    _linter_on_path()
    from outreach import parse_prospect_pack

    try:
        _version, blocks, _meta = parse_prospect_pack(text)
    except Exception:  # noqa: BLE001
        cells["touch_1_body"] = UNAVAILABLE + "the pack cannot be parsed"
        return cells
    if blocks:
        cells["touch_1_subject"] = getattr(blocks[0], "subject", "") or ""
        cells["touch_1_body"] = getattr(blocks[0], "body", "") or ""
    return cells
