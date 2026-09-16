"""``python -m gtm_core.prospects status`` — where the current list stands, in plain language.

Reads ``lanes-state.jsonl`` (the last ``lanes route`` run, per email) and ``latest.json``
(the cumulative account ledger) for one profile, and prints the six-way operator status
split from :mod:`gtm_core.prospect_status` plus the "needs an address" count. Reads
``lanes-state.jsonl`` directly rather than ``ready-to-load.csv``: the CSV is a courtesy
copy kept in sync by ``restamp_ready_to_load``, the JSONL is the source of truth.

Deliberately does not catch :class:`gtm_core.prospect_status.UnmappedStatus` — a
``(lane, reason)`` pair this module has never seen is a real bug in the mapping, and
should stop the run loudly rather than silently drop the row from every count.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .lanes.model import HOLD_QUESTION, QUESTION_COPY
from .prospect_paths import evals_dir
from .prospect_status import LABELS, NEXT_STEP, STATUSES, needs_address, status_of
from .prospects_state import load_latest

#: The five statuses `status_of` derives from a routed row's (lane, reason). `needs_address`
#: is the sixth STATUSES id but comes from `latest.json`, not from a routed row — it gets
#: its own block in the report rather than joining this split.
_LANE_STATUSES: tuple[str, ...] = tuple(s for s in STATUSES if s != "needs_address")

_NO_ROUTE_MESSAGE = "Nothing to show yet — run your prospecting first."
_PAUSED_FOOTER = "Nothing sends until you start a sequence in the sending tool."
_TOTAL_CAPTION = "every person in the current list"


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            val = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(val, dict):
            out.append(val)
    return out


def _row_reason(record: dict) -> str:
    """`reason` (PS5, additive) is the primary key; an old/unmigrated record carries only
    `trigger`."""
    return record.get("reason") or record.get("trigger") or ""


def _load_ledger_items(profile: str) -> list[dict]:
    """``latest.json``'s items, via `prospects_state.load_latest` — which already
    returns an empty skeleton when the file is absent and raises on a malformed one,
    so this module doesn't re-invent either of those rules."""
    return load_latest(profile).get("items", [])


def _format_report(status_counts: dict[str, int], needs_address_count: int) -> str:
    total = sum(status_counts.get(s, 0) for s in _LANE_STATUSES)
    label_width = max(len(LABELS[s]) for s in STATUSES)
    count_width = max(len(str(status_counts.get(s, 0))) for s in _LANE_STATUSES)
    count_width = max(count_width, len(str(total)), len(str(needs_address_count)))

    lines = [
        f"{LABELS[s]:<{label_width}}  {status_counts.get(s, 0):>{count_width}}   {NEXT_STEP[s]}"
        for s in _LANE_STATUSES
    ]
    lines.append(f"{'':<{label_width}}  {'─' * count_width}")
    lines.append(f"{'':<{label_width}}  {total:>{count_width}}   {_TOTAL_CAPTION}")
    lines.append("")
    lines.append(
        f"{LABELS['needs_address']:<{label_width}}  "
        f"{needs_address_count:>{count_width}}   {NEXT_STEP['needs_address']}"
    )
    lines.append(_PAUSED_FOOTER)
    return "\n".join(lines)


def _print_why(records: list[dict], why: str) -> None:
    """The `--why waiting_on_you` breakdown: distinct QUESTIONs, grouped, with counts.

    The one place a technical-ish detail is acceptable (PS8) — an explicit opt-in, not
    the default view. Other statuses group by reason directly.
    """
    triggers = [
        _row_reason(r) for r in records if status_of(r.get("lane") or "", _row_reason(r)) == why
    ]
    if why == "waiting_on_you":
        question_counts = Counter(HOLD_QUESTION.get(t, t) for t in triggers)
        print(f"{LABELS[why]} — {len(triggers)} — by question:")
        for question_id, count in sorted(question_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            title = QUESTION_COPY.get(question_id, (question_id, {}))[0]
            print(f"  {count:>4}  {title}")
    else:
        reason_counts = Counter(triggers)
        print(f"{LABELS[why]} — {len(triggers)} — by reason:")
        for reason, count in sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {count:>4}  {reason}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="gtm_core.prospect_status_cli",
        description="Where does the current list stand, in plain language.",
    )
    p.add_argument("--profile", required=True)
    p.add_argument(
        "--why",
        choices=[s for s in STATUSES if s != "needs_address"],
        default=None,
        help="break waiting_on_you (or another lane-derived status) down by question",
    )
    args = p.parse_args(argv)

    state_file = evals_dir(args.profile) / "lanes-state.jsonl"
    if not state_file.is_file():
        print(_NO_ROUTE_MESSAGE)
        return 1

    records = _read_jsonl(state_file)

    if args.why:
        _print_why(records, args.why)
        return 0

    status_counts = Counter(status_of(r.get("lane") or "", _row_reason(r)) for r in records)
    needs_count = sum(1 for item in _load_ledger_items(args.profile) if needs_address(item))
    print(_format_report(dict(status_counts), needs_count))
    return 0


if __name__ == "__main__":
    sys.exit(main())
