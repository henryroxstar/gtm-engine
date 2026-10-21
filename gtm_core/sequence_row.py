"""One sequencer row, normalised — the ONE boundary where snapshot rows enter the page.

Split out of :mod:`gtm_core.prospects_dashboard` (at its §R10 ceiling) on 2026-10-02, with the
name it had there kept as an import: ``prospects_dashboard._normalize_seq`` is what the loader, the
refresh command's page gate and the tests reach, and it is this function.

Stdlib-only, like its caller. The contract and the reason for it (a snapshot row with odd types
must never crash a render) are in :func:`normalize_seq` and in
``tests/contracts/test_dashboard_loader_odd_rows.py``.
"""

from __future__ import annotations

import math

from gtm_core.sequence_snapshot_format import row_id

#: A sequencer counter with more digits than this is not a count of people or emails, whatever
#: else it is. It is REFUSED (read as 0 for arithmetic, named in the row's ``refused`` list,
#: shown as an em dash) instead of being printed as though the sending tool had said it.
MAX_COUNTER_DIGITS = 15


def _count(v) -> tuple[int, bool]:
    """``(value, refused)`` for one counter of a snapshot row.

    Absent or blank is not a refusal: a figure the sending tool did not send reads 0 exactly as
    it always did. Everything else must be a finite, non-negative number under
    ``MAX_COUNTER_DIGITS`` digits. A bool, text that is not a number, ``inf``/``nan``, a negative,
    a 309-digit integer (which ``int(float(...))`` used to raise ``OverflowError`` on) and an
    integer past the interpreter's own digit limit are all refused, never raised.
    """
    if v is None or (isinstance(v, str) and not v.strip()):
        return 0, False
    if isinstance(v, bool):
        return 0, True
    try:
        n = float(str(v).strip())
    except (TypeError, ValueError):
        return 0, True
    if not math.isfinite(n) or n < 0 or n >= 10**MAX_COUNTER_DIGITS:
        return 0, True
    return int(n), False


def _int(v) -> int:
    return _count(v)[0]


def _text(v) -> str:
    """A name or a status as the sending tool gave it, or ``""`` when it is not text — a list or
    a number here has no reading a page could print, and ``.strip()`` on it is a crash."""
    return v if isinstance(v, str) else ""


def _steps(v) -> int:
    """The sequence's email-step count from the sending tool's list, or ``0`` when it is
    absent or unreadable. ``0`` means "not known", never "no emails": a goal built on it is
    left out (``campaigns_dashboard._auto_targets``), so it cannot plan zero emails."""
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 < v <= 50 else 0


def normalize_seq(d: dict) -> dict:
    """Flatten a sequence's live stats into the curated set the page shows.

    Accepts EITHER a raw Saleshandy ``get_sequence_stats`` payload (``prospects``
    list + ``emails``) OR an already-flat dict the skill assembled — so the skill
    layer can drop the MCP response verbatim without reshaping it.

    This is the ONE boundary where snapshot rows enter the page, so it is total: whatever types
    the file holds, the row it returns has a string ``id`` (the writer's own
    :func:`~gtm_core.sequence_snapshot_format.row_id`, so a stamp is looked up under the key it
    was written under), string ``name`` and ``status``, and counters that are small integers.
    A counter that cannot be read as one is named in ``refused`` (only present when non-empty).
    """
    refused: set[str] = set()

    def n(field: str, value) -> int:
        count, bad = _count(value)
        if bad:
            refused.add(field)
        return count

    if "prospects" in d and isinstance(d.get("prospects"), list):
        first = (d["prospects"] or [{}])[0]
        p = first if isinstance(first, dict) else {}
        emails = d.get("emails")
        status_block = emails.get("status") if isinstance(emails, dict) else None
        est = status_block if isinstance(status_block, dict) else {}
        # Presence of a bounce key -- not its value -- decides the source; missing or blank never reads as a genuine 0.
        has_bounce_figure = isinstance(status_block, dict) and any(
            est.get(k) not in (None, "")
            for k in ("bounced", "hardBounced", "softBounced", "blockBounced")
        )
        if has_bounce_figure:
            bounced = n("bounced", est.get("bounced")) or (
                n("bounced", est.get("hardBounced"))
                + n("bounced", est.get("softBounced"))
                + n("bounced", est.get("blockBounced"))
            )
            bounce_source = "emails"
        else:
            bounced = n("bounced", p.get("bounced"))
            bounce_source = "prospects"
        row = {
            "id": row_id(d),
            "name": _text(d.get("sequenceName", "")),
            "status": _text(d.get("status", "")),
            "steps": _steps(d.get("steps")),
            "loaded": n("loaded", p.get("total")),
            "sent": n("sent", p.get("contacted")),
            "pending": n("pending", p.get("upcoming")) + n("pending", p.get("waiting")),
            "delivered": n("delivered", est.get("delivered")),
            "opened": n("opened", p.get("open")) or n("opened", est.get("opened")),
            "replied": n("replied", p.get("replied")) or n("replied", est.get("replied")),
            "bounced": bounced,
            "bounce_source": bounce_source,
            "interested": n("interested", p.get("interested")),
            "not_interested": n("not_interested", p.get("notInterested")),
            "not_now": n("not_now", p.get("notNow")),
            "out_of_office": n("out_of_office", p.get("outOfOffice")),
            "unsubscribed": n("unsubscribed", p.get("unsubscribed")),
            "do_not_contact": n("do_not_contact", p.get("doNotContact")),
            "meetings": n("meetings", p.get("meetingBooked")),
            "deal_value": n("deal_value", p.get("meetingBookedDealValue"))
            + n("deal_value", p.get("interestedDealValue")),
        }
        if refused:
            row["refused"] = sorted(refused)
        return row
    # already flat
    keys = (
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
    flat = {
        "id": row_id(d),
        "name": _text(d.get("name") or d.get("sequenceName")),
        "status": _text(d.get("status")),
        "steps": _steps(d.get("steps")),
    }
    flat.update({k: n(k, d.get(k)) for k in keys})
    # A payload row with no ``prospects`` list still carries ``emails.status``; the writer reads
    # ``delivered`` from there, so the page must too or it shows 0 where the writer says 40.
    emails = d.get("emails")
    block = emails.get("status") if isinstance(emails, dict) else None
    if d.get("delivered") in (None, "") and isinstance(block, dict):
        flat["delivered"] = n("delivered", block.get("delivered"))
    flat["bounce_source"] = None  # no per-email block on the flat path -> rate is "not available"
    if refused:
        flat["refused"] = sorted(refused)
    return flat
