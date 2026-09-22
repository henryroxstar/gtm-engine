"""``python -m gtm_core.prospects status`` — where the current list stands, in plain language.

Reads ``lanes-state.jsonl`` (the last ``lanes route`` run, per email) and ``latest.json``
(the cumulative account ledger) for one profile, and prints:

* the **accounts** block (companies) — :mod:`gtm_core.prospect_status_receipt`;
* the **contacts** table (people) — the six-way status from :mod:`gtm_core.prospect_status`;
* the two ledger-only lines: contacts with no address, and contacts whose address is still
  being checked.

The prospect skill pastes this block verbatim into every run, so every number is a claim made
to a person. Three rules follow. **One source per claim:** the ACTION REQUIRED banner IS the
"Waiting on you" count — the same routed state, never a second derivation — and the accounts
block's held/ready are that same routed state joined to the ledger. **Units are labelled:**
accounts and contacts are different things and the headings say which. **Never a traceback:**
both input files are data (§R5). A state record this build cannot map is counted on a visible
"Unrecognised" line (with a warning on stderr); an unreadable ledger, or a routed-state file
with a line that is not a record (or bytes that are not text), is one clear line and exit 2 — this step is mandatory in every run, so it has to say what is wrong rather than die.

Reads ``lanes-state.jsonl`` directly rather than ``ready-to-load.csv``: the CSV is a courtesy
copy kept in sync by ``restamp_ready_to_load``, the JSONL is the source of truth. Writes
nothing.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from .lanes.decisions import StateError, newest_sheet, read_state_records
from .lanes.model import HOLD_QUESTION, QUESTION_COPY
from .paths import resolve_content_root
from .prospect_paths import evals_dir
from .prospect_status import (
    CHECKING_ADDRESS_LABEL,
    CHECKING_ADDRESS_NEXT_STEP,
    LABELS,
    NEXT_STEP,
    STATUSES,
    UNRECOGNISED,
    UNRECOGNISED_LABEL,
    UNRECOGNISED_NEXT_STEP,
    awaiting_verification,
    needs_address,
    status_or_unrecognised,
)
from .prospect_status_receipt import (
    compute_attrition_receipt,
    cross_check,
    format_attrition_receipt,
)
from .prospects_state import load_latest

#: The five statuses `status_of` derives from a routed row's (lane, reason). `needs_address`
#: is the sixth STATUSES id but comes from `latest.json`, not from a routed row — it gets
#: its own block in the report rather than joining this split.
_LANE_STATUSES: tuple[str, ...] = tuple(s for s in STATUSES if s != "needs_address")

_NO_ROUTE_MESSAGE = "Nothing to show yet — run your prospecting first."
_PAUSED_FOOTER = "Nothing sends until you start a sequence in the sending tool."
_TOTAL_CAPTION = "every person in the current list"
_CONTACTS_HEADING = "Contacts — by status (people, not companies):"
_BUILD_SHEET_HINT = "run `python -m gtm_core.lanes route …` to build it"


def _row_reason(record: dict) -> str:
    """`reason` (PS5, additive) is the primary key; an old/unmigrated record carries only
    `trigger`."""
    return str(record.get("reason") or record.get("trigger") or "")


def _status(record: dict) -> str:
    return status_or_unrecognised(str(record.get("lane") or ""), _row_reason(record))


def _load_ledger_items(profile: str) -> list[dict]:
    """``latest.json``'s items, via `prospects_state.load_latest` — which already returns an
    empty skeleton when the file is absent and raises on a malformed one, so this module
    doesn't re-invent either rule. Entries that are not objects are dropped: a ledger row is
    data, and one bad row must not take the whole report down."""
    return [it for it in load_latest(profile).get("items", []) if isinstance(it, dict)]


def _banner(waiting: int, profile: str) -> str:
    """The ACTION REQUIRED line. ``waiting`` IS the "Waiting on you" count — it is passed in,
    never re-derived — and the sheet named is one that exists on disk."""
    people = "contact is" if waiting == 1 else "contacts are"
    sheet = newest_sheet(profile)
    if sheet is None:
        where = f"No review sheet exists yet — {_BUILD_SHEET_HINT}."
    else:
        try:
            shown = sheet.relative_to(resolve_content_root())
        except ValueError:
            shown = sheet
        where = f"Review sheet: {shown}"
    return f"> [!WARNING] ACTION REQUIRED: {waiting} {people} waiting on your decision. {where}\n"


def _held_back_contacts(profile: str) -> int:
    """People the last build held OUT of the list until their address is confirmed.

    The build reports them ("N verifying") and then they appear nowhere in this block — the
    list only ever holds confirmed addresses — so without a line they vanish between screens.
    """
    import csv

    from .prospects_consolidate.paths import needs_verification_path

    path = needs_verification_path(profile)
    if not path.is_file():
        return 0
    with path.open(newline="", encoding="utf-8") as fh:
        return sum(1 for _row in csv.DictReader(fh))


def _format_report(
    counts: dict[str, int],
    needs_address_count: int,
    checking_address_count: int = 0,
    held_back_count: int = 0,
) -> str:
    """The contacts table, then the two ledger-only lines (which are NOT part of its total).

    Everything this returns is pasted to the operator, so it is scanned for pipeline
    vocabulary (``tests/lint/test_operator_vocabulary.py``) — plain words only in here.
    """
    ledger_lines = [
        (LABELS["needs_address"], needs_address_count, NEXT_STEP["needs_address"]),
        (CHECKING_ADDRESS_LABEL, checking_address_count, CHECKING_ADDRESS_NEXT_STEP),
    ]
    if held_back_count:
        ledger_lines.append(
            (
                "Held back for a check",
                held_back_count,
                "people found but not in the list above — the machine's; they join once "
                "the address is confirmed",
            )
        )
    rows = [(LABELS[s], counts.get(s, 0), NEXT_STEP[s]) for s in _LANE_STATUSES]
    if counts.get(UNRECOGNISED):
        rows.append((UNRECOGNISED_LABEL, counts[UNRECOGNISED], UNRECOGNISED_NEXT_STEP))
    total = sum(n for _label, n, _step in rows)
    label_width = max(len(label) for label, _n, _step in [*rows, *ledger_lines])
    count_width = max(len(str(n)) for n in [total, *(n for _l, n, _s in [*rows, *ledger_lines])])

    def line(label: str, count: int, step: str) -> str:
        return f"{label:<{label_width}}  {count:>{count_width}}   {step}"

    lines = [_CONTACTS_HEADING, *(line(*row) for row in rows)]
    lines.append(f"{'':<{label_width}}  {'─' * count_width}")
    lines.append(line("", total, _TOTAL_CAPTION))
    lines.append("")
    lines += [line(*row) for row in ledger_lines]
    lines.append(_PAUSED_FOOTER)
    return "\n".join(lines)


def _print_why(records: list[dict], why: str) -> None:
    """The `--why waiting_on_you` breakdown: distinct QUESTIONs, grouped, with counts.

    The one place a technical-ish detail is acceptable (PS8) — an explicit opt-in, not
    the default view. Other statuses group by reason directly.
    """
    triggers = [_row_reason(r) for r in records if _status(r) == why]
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


def _routed_contacts(records: list[dict]) -> list[dict]:
    """What the accounts block joins on: each record's identity plus its derived status."""
    return [
        {
            "email": r.get("email"),
            "company": r.get("company"),
            "company_domain": r.get("company_domain"),
            "account_id": r.get("account_id"),
            "status": _status(r),
        }
        for r in records
    ]


def _print_report(profile: str, records: list[dict], ledger_items: list[dict]) -> None:
    contacts = _routed_contacts(records)
    counts = Counter(c["status"] for c in contacts)
    receipt = compute_attrition_receipt(ledger_items, contacts)

    waiting = counts.get("waiting_on_you", 0)
    if waiting:
        print(_banner(waiting, profile))
    print(format_attrition_receipt(receipt))
    print("")
    print(
        _format_report(
            dict(counts),
            sum(1 for it in ledger_items if needs_address(it)),
            sum(1 for it in ledger_items if awaiting_verification(it)),
            _held_back_contacts(profile),
        )
    )
    for problem in cross_check(receipt, dict(counts), len(records)):
        print(problem)
    if counts.get(UNRECOGNISED):
        print(
            f"warning: {counts[UNRECOGNISED]} routed record(s) carry a reason this build does "
            f"not recognise — counted as {UNRECOGNISED_LABEL}; re-run `lanes route`",
            file=sys.stderr,
        )


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

    try:
        records = read_state_records(state_file)
    except (OSError, StateError) as exc:
        # One broken line skipped is one person missing from every number below — refuse
        # rather than print a confident undercount (the same rule `lanes route` applies).
        print(f"The sorted list could not be read — {exc}", file=sys.stderr)
        return 2

    if args.why:
        _print_why(records, args.why)
        return 0

    try:
        ledger_items = _load_ledger_items(args.profile)
    except (OSError, ValueError) as exc:
        print(f"The account ledger could not be read — {exc}", file=sys.stderr)
        return 2
    _print_report(args.profile, records, ledger_items)
    return 0


if __name__ == "__main__":
    sys.exit(main())
