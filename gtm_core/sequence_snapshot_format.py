"""The shape of ``.pool/sequence-stats.json`` — shared by its writer and the dashboard's loader.

Stdlib only, so the loader can import it without pulling the writer's dependencies (or an
import cycle) in. A file this module's writer produced is format 2: the sequencer's payload
rows untouched, plus a per-sequence date, the sequences still wearing the file's single date
instead of their own, any send counter that fell between two refreshes, any row a refresh re-dated
with figures identical to the ones it already held (``restamped``: the stamp it had before), a hash
of each payload file the row came from, and a hash of the whole body.

The body hash is a record of what the refresh command wrote, not proof the figures are true:
it lets a later reader see that the file was changed by something other than that command.
A ``falls`` or ``restamped`` entry that is not in the closed shape below is read the same way a
hand edit is: the command cannot have written it, so the file is ``edited`` and the page's age is
unknown.
"""

from __future__ import annotations

import hashlib
import json

FORMAT = 2
#: The name of the file, under the profile's ``.pool`` folder. One home for the writer, its
#: ``status`` and the messages that quote it.
FILE_NAME = "sequence-stats.json"
#: Said of a row a refresh re-dated whose figures are the same as the ones it already held.
IDENTICAL = "figures identical to the previous record"
#: The counters whose fall between two refreshes is worth a sentence (they only ever rise).
FALL_FIELDS = ("sent", "delivered")


def row_id(item) -> str:
    """The sequence id of a payload row or a flat row, or ``""`` when it carries none."""
    if not isinstance(item, dict):
        return ""
    value = item.get("sequenceId") or item.get("id")
    if value in (None, ""):
        return ""
    try:
        return str(value).strip()
    except ValueError:  # an int past the interpreter's digit limit: no usable id, not a crash
        return ""


def _int(value) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError, OverflowError):
        return None


def figures_of(row: dict) -> dict[str, int | None]:
    """``{sent, delivered}`` of a raw payload row (it has a ``prospects`` list) or a flat row.

    ``None`` means the row does not carry that figure — never zero — so a fall is only ever
    reported between two figures that both exist. A row with no ``prospects`` list still
    reports ``delivered`` when it carries ``emails.status.delivered``: that is the counter the
    sends ledger reads, so a fall in it must not go unseen because the rest of the row is thin.
    """
    emails = row.get("emails") if isinstance(row.get("emails"), dict) else {}
    status = emails.get("status") if isinstance(emails.get("status"), dict) else {}
    if isinstance(row.get("prospects"), list):
        p0 = (
            row["prospects"][0]
            if row["prospects"] and isinstance(row["prospects"][0], dict)
            else {}
        )
        return {"sent": _int(p0.get("contacted")), "delivered": _int(status.get("delivered"))}
    delivered = _int(row.get("delivered"))
    return {
        "sent": _int(row.get("sent")),
        "delivered": delivered if delivered is not None else _int(status.get("delivered")),
    }


def body_digest(raw: dict) -> str:
    body = {k: v for k, v in raw.items() if k != "body_sha256"}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    # A hand-edited file can hold a lone surrogate the writer would have refused; hash it
    # rather than crash, so the page reads it as edited instead of failing to build.
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _well_formed_changes(fall) -> bool:
    changes = fall.get("changes") if isinstance(fall, dict) else None
    return (
        isinstance(changes, list)
        and bool(changes)
        and all(
            isinstance(c, dict)
            and c.get("field") in FALL_FIELDS
            and _count(c.get("was"))
            and _count(c.get("now"))
            for c in changes
        )
    )


def _well_formed_fall(fall) -> bool:
    """The one shape the writer produces: ``{"on": str, "changes": [{field, was, now}, …]}``."""
    return _well_formed_changes(fall) and isinstance(fall.get("on"), str)


def fall_sentence(fall: dict) -> str:
    """One plain sentence per fallen counter, e.g. "sent is 31 now, was 40 at the last snapshot…".

    A fall that is not in the shape the writer produces says nothing rather than raise: the
    loader already marks such a file edited, and a caller must not crash on one it was handed.
    """
    if not _well_formed_changes(fall):
        return ""
    return " ".join(
        f"{c['field']} is {c['now']} now, was {c['was']} at the last snapshot. "
        "Check the sending tool if that is unexpected."
        for c in fall["changes"]
    )


def _checked_restamped(value) -> tuple[dict, bool]:
    """``(restamped, ok)`` — ``{id: the stamp it had before}``; empty when ``ok`` is False."""
    if value is None:
        return {}, True
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and v for k, v in value.items()
    ):
        return {}, False
    return dict(value), True


def _checked_falls(value) -> tuple[dict, bool]:
    """``(falls, ok)`` — ``falls`` is empty when ``ok`` is False. Absent means none, not bad."""
    if value is None:
        return {}, True
    if not isinstance(value, dict) or not all(_well_formed_fall(f) for f in value.values()):
        return {}, False
    return {str(k): v for k, v in value.items()}, True


def file_meta(raw) -> dict:
    """What a parsed ``sequence-stats.json`` says about itself.

    ``stamps`` maps a sequence id to the date its figures were fetched. A legacy dict file
    (``sequences`` plus one top-level ``fetched``) gives every row that top-level date and lists
    them all as ``inherited`` — the date is the file's, not the row's. A bare list has no date
    at all, so its rows are unstamped. ``edited`` is a format-2 file whose body no longer
    matches the hash its writer recorded, or whose ``falls`` or ``restamped`` record the writer could
    not have written (``falls_invalid`` says which). ``restamped`` maps a sequence id to the stamp
    it carried before the last refresh that re-dated it with figures identical to the old ones.
    """
    rows = raw.get("sequences") if isinstance(raw, dict) else raw
    items = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    ids = [row_id(r) for r in items]
    meta = {
        "tool_written": False,
        "edited": False,
        "falls_invalid": False,
        "stamps": {},
        "inherited": [],
        "falls": {},
        "restamped": {},
        "payload_sha256": {},
        "file_date": None,
        "ids": ids,
    }
    if not isinstance(raw, dict):
        return meta
    top = raw.get("fetched")
    meta["file_date"] = top if isinstance(top, str) and top else None
    if raw.get("format") == FORMAT:
        stamps = raw.get("stamps") if isinstance(raw.get("stamps"), dict) else {}
        inherited = raw.get("inherited") if isinstance(raw.get("inherited"), list) else []
        falls, falls_ok = _checked_falls(raw.get("falls"))
        restamped, restamped_ok = _checked_restamped(raw.get("restamped"))
        notes_ok = falls_ok and restamped_ok
        hashes = raw.get("payload_sha256") if isinstance(raw.get("payload_sha256"), dict) else {}
        meta.update(
            tool_written=True,
            edited=raw.get("body_sha256") != body_digest(raw) or not notes_ok,
            falls_invalid=not notes_ok,
            stamps={str(k): v for k, v in stamps.items() if isinstance(v, str)},
            inherited=[str(i) for i in inherited],
            falls=falls,
            restamped=restamped,
            payload_sha256={str(k): v for k, v in hashes.items() if isinstance(v, str)},
        )
    elif meta["file_date"]:
        meta["stamps"] = {i: meta["file_date"] for i in ids if i}
        meta["inherited"] = [i for i in ids if i]
    return meta
