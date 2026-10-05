"""What ``history.jsonl`` says happened to each sequencer sequence: loads, switch-ons, pauses.

The ledger half of :mod:`gtm_core.sequencer_reconcile`, split out so the comparison and the reading
of the ledger can each be tested on their own. Stdlib plus :class:`PayloadError`.

**What "accounted for" means.** Per sequence id: the *largest* ``leads_enrolled`` any
``sequence_staged`` event (or ``new_sequences[].enrolled`` of a rebuild or seat split) recorded,
**plus** the sum of the loads that are one event each — ``enrolled`` (``leads_enrolled``, or the
dispatcher's ``lead_count``), ``prospects_enrolled`` (``enrolled``) and
``sequence_loaded_unrecorded`` (``prospects_loaded``). ``sequence_staged`` is a running snapshot,
not an increment: one sequence was staged at 4 people and again at 9 when 5 more were added, and it
holds 9. A staged snapshot followed by an ``enrolled`` event for the very same people would count
them twice and hide an equal unrecorded load, which is why the reconciler also reports a ledger
that claims *more* than the sequencer holds.

**On and off.** An activation is ``sequence_activated_by_operator``, a ``sequences_activated``
batch row naming the id, or a ``sequence_staged`` row recorded as ``status: active``; a pause is
``sequence_paused_by_operator`` or a ``sequences_paused_and_contacted_suppressed`` batch row. The
*last* of the two, in ledger order, is the state history believes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from gtm_core.sequencer_stats_read import PayloadError

ACTIVATED = "activated"
PAUSED = "paused"
_STATE_EVENTS = {
    "sequence_activated_by_operator": ACTIVATED,
    "sequence_paused_by_operator": PAUSED,
}
#: Batch events that name their sequences under ``sequences: [{"id": ...}]``.
_BATCH_EVENTS = {
    "sequences_activated": ACTIVATED,
    "sequences_paused_and_contacted_suppressed": PAUSED,
}
#: One event, one load: the keys that carry the count, first one present wins.
_LOAD_EVENTS = {
    "enrolled": ("leads_enrolled", "lead_count"),
    "prospects_enrolled": ("enrolled",),
    "sequence_loaded_unrecorded": ("prospects_loaded",),
}
_REBUILD_EVENTS = ("sequence_rebuilt", "sequence_seat_split")


@dataclass
class SeqHistory:
    """What the ledger says about one sequence id."""

    snapshot_max: int = 0
    added: int = 0
    lifecycle: list[tuple[int, str]] = field(default_factory=list)

    @property
    def accounted(self) -> int:
        return self.snapshot_max + self.added

    @property
    def last_state(self) -> str | None:
        return self.lifecycle[-1][1] if self.lifecycle else None


def read_history(path: Path) -> list[dict]:
    """Every row of ``history.jsonl``, in order. A line that is not a JSON object stops the run.

    :meth:`gtm_core.ledgers.Ledgers.iter_history` skips a corrupt line, which suits a reader that
    wants what it can get; a reconciler that skipped one would read a smaller ledger and call it
    complete.
    """
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise PayloadError(f"history.jsonl is unreadable ({type(exc).__name__})") from exc
    rows: list[dict] = []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PayloadError(f"history.jsonl line {n} is not valid JSON") from exc
        if not isinstance(row, dict):
            raise PayloadError(f"history.jsonl line {n} is not a JSON object")
        rows.append(row)
    return rows


def _whole(value) -> int:
    """A count a ledger row carries: a number, or digits as text. Anything else counts as 0."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def _ids(items) -> list[str]:
    if not isinstance(items, list):
        return []
    return [i["id"] for i in items if isinstance(i, dict) and isinstance(i.get("id"), str)]


def _note_row(index: dict[str, SeqHistory], line_no: int, row: dict) -> None:
    event, sid = row.get("event"), row.get("sequence_id")
    sid = sid if isinstance(sid, str) else None
    if event in _STATE_EVENTS and sid:
        index.setdefault(sid, SeqHistory()).lifecycle.append((line_no, _STATE_EVENTS[event]))
    elif event in _BATCH_EVENTS:
        for batch_id in _ids(row.get("sequences")):
            index.setdefault(batch_id, SeqHistory()).lifecycle.append(
                (line_no, _BATCH_EVENTS[event])
            )
    elif event in _LOAD_EVENTS and sid:
        counts = (_whole(row.get(key)) for key in _LOAD_EVENTS[event])
        index.setdefault(sid, SeqHistory()).added += next((n for n in counts if n), 0)
    elif event == "sequence_staged" and sid:
        hist = index.setdefault(sid, SeqHistory())
        hist.snapshot_max = max(hist.snapshot_max, _whole(row.get("leads_enrolled")))
        if row.get("status") == "active":
            hist.lifecycle.append((line_no, ACTIVATED))
    elif event in _REBUILD_EVENTS:
        for item in row.get("new_sequences") or []:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                hist = index.setdefault(item["id"], SeqHistory())
                hist.snapshot_max = max(hist.snapshot_max, _whole(item.get("enrolled")))


def index_history(rows: list[dict]) -> dict[str, SeqHistory]:
    """``sequence id -> SeqHistory`` over the ledger's rows (line numbers are 1-based)."""
    index: dict[str, SeqHistory] = {}
    for line_no, row in enumerate(rows, 1):
        _note_row(index, line_no, row)
    return index
