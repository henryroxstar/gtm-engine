"""Shared address key and per-event opt-out sets — for the Results page and the DNC dispatcher.

Two callers need to read the same three ledger events keyed the same way: the campaign
Results page (how many people opted out, how many of them are already on the do-not-contact
list) and `agent.dnc_dispatch.open_candidates` (the whole of the brain's permitted vocabulary
for a gated DNC add). Before this module existed, either caller could drift onto its own
notion of "the address" or its own reading of a row's event without the other noticing. This
module is the one place that defines the key and partitions the rows, so the two callers can
never quietly disagree on either — what is shared is the KEY and the SETS, never a formula:
the dispatcher keeps its own `(detected | unreadable) - added` narrowing formula, unchanged,
and the page computes its own `len(detected)` / `len(detected & added)` from the exact same
three sets.

It lives in its own module, not `gtm_core.optout_watch`, because that module is already at
its §R10 complexity ceiling (641/641 lines) — see `tests/lint/complexity_allowlist.txt`.

This module decides NOTHING. It does not compute an open/candidate set, does not decide who
is contactable or who should be added anywhere, and does no I/O of its own — it only sorts
rows the caller already read (e.g. via `Ledgers.iter_history()`) into sets, by event, under
one address key. Pure stdlib.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: The three ledger events this module partitions rows into. Any other `event` is ignored
#: entirely — not counted anywhere, not even in `unattributable`.
_DETECTED_EVENT = "optout_detected"
_UNREADABLE_EVENT = "optout_unreadable"

#: Public: this is the event name `agent.dnc_dispatch` WRITES once a read-back confirms an
#: add. Exported so the writer and the reader share one spelling of "already mirrored"
#: rather than each hardcoding `"dnc_added"` separately.
DNC_ADDED_EVENT = "dnc_added"

_TRACKED_EVENTS = frozenset({_DETECTED_EVENT, _UNREADABLE_EVENT, DNC_ADDED_EVENT})


def optout_key(value: Any) -> str:
    """The one address key every opt-out reader shares: trimmed and lower-cased.

    Never raises on a non-string or missing `value` — `None` and any other falsy input
    become the empty string, which `optout_sets` treats as unattributable rather than as
    some other row's key.
    """
    return str(value or "").strip().lower()


def optout_sets(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[set[str], set[str], set[str], int]:
    """Partition history rows into `(detected, unreadable, added, unattributable)`.

    - `event == "optout_detected"` -> the row's key joins `detected`.
    - `event == "optout_unreadable"` -> the row's key joins `unreadable`.
    - `event == "dnc_added"` -> the row's key joins `added`.
    - any other `event` is ignored — not counted anywhere.
    - a row whose event IS one of the three above but whose `optout_key` is empty
      increments `unattributable` instead of joining a set, so an address-less opt-out
      row is reported rather than silently dropped or merged into another row's key.

    Each of the first three return values is a `set[str]`, so re-recording the same event
    for the same address — including case/whitespace variants, since the key is
    `optout_key` — still counts once. This function decides nothing about who is
    contactable or who should be added anywhere; callers (the Results page, and
    `agent.dnc_dispatch.open_candidates`) own that.
    """
    detected: set[str] = set()
    unreadable: set[str] = set()
    added: set[str] = set()
    unattributable = 0

    for row in rows:
        # Address BEFORE event, deliberately — matches HEAD's `open_candidates`, which
        # never tests `event` at all when the address is empty. A corrupt or adversarial
        # ledger line still decodes as valid JSON but can carry any type for `event` (a
        # list, a dict); testing `event in _TRACKED_EVENTS` first would raise
        # `TypeError: unhashable type` on such a row instead of skipping it. The
        # `isinstance` guard below is what lets an empty-keyed row still count towards
        # `unattributable` without reintroducing that same crash.
        event = row.get("event")
        key = optout_key(row.get("email"))
        if not key:
            if isinstance(event, str) and event in _TRACKED_EVENTS:
                unattributable += 1
            continue
        if event not in _TRACKED_EVENTS:
            continue
        if event == _DETECTED_EVENT:
            detected.add(key)
        elif event == _UNREADABLE_EVENT:
            unreadable.add(key)
        elif event == DNC_ADDED_EVENT:
            added.add(key)

    return detected, unreadable, added, unattributable
