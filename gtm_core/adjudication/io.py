from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path

from .model import Adjudication

# --- I/O -----------------------------------------------------------------


def read_records(path: Path) -> list[Adjudication]:
    out: list[Adjudication] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        d = json.loads(line)
        # `repair_attempt` is read with a sentinel, never `int(... or 0)`: a record that
        # never recorded the field is genuinely unknown, and defaulting it to 0 would
        # restart a row already at the cap — the unbounded-loop bug in disguise.
        raw_attempt = d.get("repair_attempt")
        # Same sentinel discipline as `repair_attempt`, for the same reason: a record written
        # before this field existed is UNKNOWN, not "calibrated=False". Coercing unknown to
        # False would invent a measurement nobody made — the mirror of the bug the field is
        # here to stop, which was reading an unmeasured verdict as a measured one.
        raw_calibrated = d.get("calibrated")
        out.append(
            Adjudication(
                email=d.get("email", ""),
                verdict=(d.get("verdict") or "").strip().lower(),
                score=int(d.get("score") or 0),
                stratum=d.get("stratum", ""),
                touch=int(d.get("touch") or 1),
                defect_class=d.get("defect_class", ""),
                evidence=d.get("evidence", ""),
                note=d.get("note", ""),
                row_id=d.get("row_id", ""),
                body_hash=d.get("body_hash", ""),
                repair_attempt=(None if raw_attempt is None else int(raw_attempt)),
                repaired=bool(d.get("repaired", False)),
                unscored=bool(d.get("unscored", False)),
                backend=d.get("backend", ""),
                judge_batch=int(d.get("judge_batch") or 1),
                rubric=d.get("rubric", ""),
                rubric_version=d.get("rubric_version", ""),
                grounding=d.get("grounding", ""),
                calibrated=(None if raw_calibrated is None else bool(raw_calibrated)),
            )
        )
    return out


def write_records(records: Iterable[Adjudication], path: Path) -> Path:
    """Write adjudication records as JSONL. Round-trips through :func:`read_records`."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "".join(json.dumps(a.to_dict()) + "\n" for a in records),
        encoding="utf-8",
    )
    return out


def _read_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _read_fieldnames(path: Path) -> list[str]:
    """The CSV's declared header, which is not the same as ``rows[0]``'s keys."""
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh).fieldnames or [])
