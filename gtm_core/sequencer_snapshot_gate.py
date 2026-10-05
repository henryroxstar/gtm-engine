"""Would the status page read this figures document? The writer asks before it writes.

The page is what reads ``sequence-stats.json``, and the writer used to check the *file* ("nothing
the file cannot hold") and not its *readers*: an integer ``sequenceId``, a non-text name or a
309-digit counter was accepted, and every later refresh then crashed in the page — the rollup
render sits outside the per-page guard, so no page was refreshed at all.

The rules in :mod:`sequence_payload_check` refuse those shapes by name. This is the second,
independent half: the merged document is run through **the page's own row reader**
(``prospects_dashboard._normalize_seq``) and the first things the page does with the rows
(``figure_ages.per_id``, ``health.figures_state``, the repeated-row and reconcile lookups), in memory,
before the lock-held write. A shape nobody wrote a rule for still stops the write if the reader
raises on it; a reader that is later made tolerant does not re-admit a shape the rules refuse.

It reads the *merged* document, not just the incoming rows, because a row already in the file (a
legacy file the writer never validated) poisons the page just as an incoming one does.
"""

from __future__ import annotations

from datetime import datetime

from gtm_core import sequence_payload_check as rules
from gtm_core.page_inputs_io import printable
from gtm_core.sequence_snapshot_format import file_meta, row_id


def _who(raw) -> str:
    return printable(row_id(raw) or "?")


def _raw_problem(raw) -> str | None:
    """A row's own id and name, as written — whatever the page's reader would later make of them.

    The id key is ``sequenceId`` (a payload row) or ``id`` (a row of an older file). It must be
    text: a reader that quietly turns ``7`` into ``"7"`` is a reader that agrees with the writer
    today and not tomorrow.
    """
    if not isinstance(raw, dict):
        return "a row that is not an object"
    key = raw.get("sequenceId") or raw.get("id")
    if not isinstance(key, str):
        return f"sequence {_who(raw)}: its id is {type(key).__name__}, not text"
    name = raw.get("sequenceName", raw.get("name"))
    if not rules.usable_name(name):
        return f"sequence {_who(raw)}: its name is {type(name).__name__} or too long, not text"
    return None


def _page_rows(raws: list) -> tuple[list[dict], str | None]:
    """The page's own reader over each row: ``(its rows, why it could not)``."""
    from gtm_core.prospects_dashboard import _normalize_seq

    rows = []
    for raw in raws:
        try:
            row = _normalize_seq(raw)
        except Exception as exc:  # the page's own reader, on whatever shape reached it
            return [], f"sequence {_who(raw)}: the page's row reader raises {type(exc).__name__}"
        rows.append(row)
    return rows, None


def page_problem(doc: dict, now: datetime) -> str | None:
    """Why the status page could not read ``doc`` (the merged format-2 document), else None."""
    from gtm_core.email_campaign_dashboard import figure_ages, health
    from gtm_core.email_campaign_dashboard.model import reconcile_snapshot
    from gtm_core.prospects_dashboard import _snapshot

    raws = doc.get("sequences") or []
    if len(raws) > rules.MAX_ROWS:
        return (
            f"it would hold {len(raws)} sequences, more than the {rules.MAX_ROWS} a page reads "
            "(--prune drops the ones this payload does not hold)"
        )
    for raw in raws:
        if why := _raw_problem(raw):
            return why
    rows, why = _page_rows(raws)
    if why:
        return why
    try:
        snap = _snapshot(
            rows,
            doc.get("fetched"),
            unreadable=False,
            skipped=0,
            source="stats",
            meta=file_meta(doc),
        )
        status = {"snapshot": snap, "sequences": rows}
        figure_ages.per_id(snap, {"campaigns": []}, now)
        health.figures_state(status, now)
        health.repeated_rows({"status": status})
        reconcile_snapshot({"campaigns": []}, status)
    except Exception as exc:
        return f"the page's figures reader raises {type(exc).__name__}"
    return None
