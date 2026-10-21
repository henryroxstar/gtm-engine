"""Is this payload one the sending-figures writer may record? Refuse before the first byte is written.

The sequencer's ``get_sequence_stats`` reply is ``{"message": …, "payload": {…one sequence…}}``.
The writer takes the rows, not the reply: ``{"sequences": [<each reply's "payload">, …]}`` — or a
bare list of rows, or one bare row. ``sequencer_sends.sequences_in`` is looser (it unwraps a
``payload`` key and quietly drops anything it cannot key); that is right for the sends ledger and
wrong for a writer whose output is a record of what was fetched, because a row dropped without a
word reads as "refreshed". So this module is the strict half, and what it accepts is always a
shape ``sequences_in`` reads as the *same rows*.

Every refusal names the problem and, where there is one, the row's index (``sequences[2]``). Text
quoted back from the payload is escaped, never raw: the payload is untrusted (§R5) and the
message is read by an agent and a terminal.

Stdlib only.
"""

from __future__ import annotations

import math

from gtm_core.sequence_snapshot_format import FORMAT

#: Keys only a file this writer made carries. A payload holding one is the figures file fed back.
SNAPSHOT_KEYS = ("body_sha256", "payload_sha256", "stamps")
#: A sequence id longer than this is not an id.
MAX_ID_LEN = 200
#: A sequence name longer than this is not a name. The page prints it in a table cell.
MAX_NAME_LEN = 300
#: More rows than this in one payload is not a sequencer's reply (a real account has dozens). It
#: also bounds what the locked merge has to hold, so a hostile payload cannot hold the lock long.
MAX_ROWS = 1000
#: A counter is at most this many digits. Fifteen decimal digits are exact in the float the page
#: reads counters through; past about 308 digits that reader raises. No real count is near either.
MAX_COUNTER_DIGITS = 15
#: A real row is four or five levels deep; anything past this is not a sequencer's reply.
MAX_DEPTH = 16
#: A payload file larger than this is refused before reading or parsing. A real 1,000-row export
#: is around 200 KB; 5 MB gives generous headroom while preventing memory exhaustion.
MAX_PAYLOAD_BYTES = 5 * 1024 * 1024

#: The counters the page and the sends ledger read, by where they sit in a payload row.
PROSPECT_COUNTERS = (
    "total",
    "contacted",
    "upcoming",
    "waiting",
    "open",
    "replied",
    "bounced",
    "interested",
    "notInterested",
    "notNow",
    "outOfOffice",
    "unsubscribed",
    "doNotContact",
    "meetingBooked",
)
#: Money, not counts: the page adds them (truncating a fraction), so they may be fractional but
#: not unbounded.
PROSPECT_AMOUNTS = ("meetingBookedDealValue", "interestedDealValue")
#: A row with no ``prospects`` list is read as an already-flat row, and these top-level keys are
#: its counters. (A row with a list is read through ``prospects[0]`` and ``emails.status``.)
FLAT_COUNTERS = (
    "loaded",
    "sent",
    "pending",
    "delivered",
    "opened",
    "replied",
    "bounced",
    "interested",
    "not_interested",
    "not_now",
    "out_of_office",
    "unsubscribed",
    "do_not_contact",
    "meetings",
    "deal_value",
)
STATUS_COUNTERS = (
    "delivered",
    "opened",
    "replied",
    "bounced",
    "hardBounced",
    "softBounced",
    "blockBounced",
)


def _shown(value, limit: int = 40) -> str:
    """A value quoted back safely: ``ascii`` escapes control characters and line breaks."""
    try:
        text = ascii(value)
    except ValueError:  # an int past the interpreter's digit limit has no repr
        return "<a number too large to show>"
    return text if len(text) <= limit else text[: limit - 1] + "…'"


def _usable_id(value) -> bool:
    """A key the page, the writer and the ledger all read the same way (none of them strips it).

    Text only. The sequencer's ids are opaque text; a number would be ``7`` to one reader and
    ``"7"`` to another, and the page sorts and joins ids as text. ``isprintable`` is what refuses
    an interior control character, a right-to-left override, a zero-width space and a non-breaking
    space: it is False for every Unicode category C* and Z* except the plain space.
    """
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_ID_LEN
        and value == value.strip()
        and value.isprintable()
    )


def usable_name(value) -> bool:
    """A name is text, or absent. The page strips and prints it; anything else raises there."""
    return value is None or (isinstance(value, str) and len(value) <= MAX_NAME_LEN)


def _whole(value) -> bool:
    """A counter as the provider types it: a whole number, or its digits as text — and short.

    The cap is on the digits and is tested before any conversion, so a 4,000-digit number is
    refused without being parsed.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 0 <= value < 10**MAX_COUNTER_DIGITS
    return (
        isinstance(value, str)
        and value.isascii()
        and value.isdigit()
        and len(value) <= MAX_COUNTER_DIGITS
    )


def _amount(value) -> bool:
    """A money figure: a finite number or numeric text, no larger than a counter may be."""
    if value in (None, ""):
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        if len(value) > MAX_COUNTER_DIGITS + 4:
            return False
        try:
            value = float(value)
        except ValueError:
            return False
    limit = 10**MAX_COUNTER_DIGITS
    if isinstance(value, int):
        return abs(value) < limit
    return isinstance(value, float) and math.isfinite(value) and abs(value) < limit


def _walk_problem(row, label: str) -> str | None:
    """The first non-finite number or unwritable text in ``row``, found without recursion."""
    stack: list[tuple[str, object, int]] = [("", row, 0)]
    while stack:
        path, node, depth = stack.pop()
        if depth > MAX_DEPTH:
            return f"{label}{path[:60]} is nested more than {MAX_DEPTH} levels deep"
        if isinstance(node, float) and not math.isfinite(node):
            return f"{label}{path} is not a finite number ({_shown(node)})"
        if isinstance(node, str):
            try:
                node.encode("utf-8")
            except UnicodeEncodeError:
                return (
                    f"{label}{path} holds text that cannot be written as UTF-8 "
                    "(a lone surrogate, usually a cut-off emoji)"
                )
        elif isinstance(node, dict):
            for key, value in node.items():
                try:
                    key.encode("utf-8")
                except UnicodeEncodeError:
                    return f"{label}{path} has a key that cannot be written as UTF-8"
                stack.append((f"{path}.{key}" if path else f".{key}", value, depth + 1))
        elif isinstance(node, list):
            stack.extend((f"{path}[{n}]", v, depth + 1) for n, v in enumerate(node))
    return None


def _counter_problem(row: dict, label: str) -> str | None:
    """The first counter, amount or shape the page's row reader would choke on, as a refusal."""
    who = f"{label} ({_shown(row['sequenceId'])})"
    emails = row.get("emails") if isinstance(row.get("emails"), dict) else {}
    status = emails.get("status") if isinstance(emails.get("status"), dict) else {}
    prospects = row.get("prospects")
    if isinstance(prospects, list):
        for n, p in enumerate(prospects):
            if not isinstance(p, dict):
                return f"{who}: prospects[{n}] is not an object (it is {type(p).__name__})"
        sources = [("prospects[0]", prospects[0] if prospects else {}, PROSPECT_COUNTERS)]
    else:
        sources = [("", row, FLAT_COUNTERS)]
    sources.append(("emails.status", status, STATUS_COUNTERS))
    for where, block, names in sources:
        for name in names:
            value = block.get(name)
            if value in (None, "") or _whole(value):
                continue
            field = f"{where}.{name}" if where else name
            return f"{who}: {field} is {_shown(value)} — a counter is a whole number of 0 or more"
    if isinstance(prospects, list) and prospects:
        for name in PROSPECT_AMOUNTS:
            if not _amount(prospects[0].get(name)):
                return (
                    f"{who}: prospects[0].{name} is {_shown(prospects[0].get(name))} — "
                    "an amount is a finite number"
                )
    if not usable_name(row.get("sequenceName")):
        return (
            f"{who}: sequenceName is {_shown(row.get('sequenceName'))} — "
            f"a name is text of at most {MAX_NAME_LEN} characters"
        )
    if not usable_name(row.get("status")):
        return (
            f"{who}: status is {_shown(row.get('status'))} — "
            f"a status is text of at most {MAX_NAME_LEN} characters"
        )
    return None


def _items(payload) -> tuple[list, str | None]:
    """The candidate rows of a payload, or the reason its shape is refused."""
    if isinstance(payload, dict):
        if payload.get("format") == FORMAT or any(k in payload for k in SNAPSHOT_KEYS):
            return [], (
                "this is the figures file itself (or a copy of it), not a fetch — feeding it "
                "back would re-date every sequence in it to now"
            )
        if "sequences" in payload:
            rows = payload["sequences"]
            if not isinstance(rows, list):
                return [], (
                    f'"sequences" is not a list (it is {type(rows).__name__}) — '
                    "put the rows in a list"
                )
            return rows, None
        if "payload" in payload and "sequenceId" not in payload:
            return [], (
                'wrapped payload: the row sits under "payload". Put each reply\'s "payload" '
                'object in a {"sequences": [...]} list'
            )
        return ([payload] if "sequenceId" in payload else []), None
    if isinstance(payload, list):
        return payload, None
    return [], f"a payload is a list of rows or an object holding one, not {type(payload).__name__}"


def check_payload(payload) -> tuple[list[dict], str | None]:
    """``(rows, None)`` for a payload the writer may record, else ``([], reason)``."""
    items, why = _items(payload)
    if why:
        return [], why
    if len(items) > MAX_ROWS:
        return [], (
            f"{len(items)} sequences in one payload — more than the {MAX_ROWS} a reply can hold; "
            "split it, or fetch fewer"
        )
    bare = isinstance(payload, dict) and "sequences" not in payload
    rows: list[dict] = []
    for n, item in enumerate(items):
        label = "the payload row" if bare else f"sequences[{n}]"
        if not isinstance(item, dict):
            return [], f"{label} is not an object (it is {type(item).__name__})"
        if not _usable_id(item.get("sequenceId")):
            return [], (
                f"{label} has no usable sequenceId (found {_shown(item.get('sequenceId'))}) — "
                "a row is keyed on it, and a row is never dropped without a word"
            )
        problem = _walk_problem(item, label) or _counter_problem(item, label)
        if problem:
            return [], problem
        rows.append(item)
    if not rows:
        return [], "no sequence with a sequenceId in it"
    return rows, None
